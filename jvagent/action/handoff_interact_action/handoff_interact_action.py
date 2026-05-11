"""Handoff interact action."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.persona.persona_action import PersonaAction
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.memory import Interaction

logger = logging.getLogger(__name__)

DIRECT_CONTACT_PROMPT = """You can reach a human representative directly using the contact details below:

Email: support@company.com
Phone / WhatsApp: +592 XXX XXXX
Office Hours: Mon-Fri, 9:00 AM - 5:00 PM

A team member will assist you as soon as possible."""


AGENT_ESCALATION_PROMPT = """I'll escalate your request to a human representative and share your conversation details with them.

A team member will review your case and reach out to you shortly using the contact information you provided."""


SCHEDULED_CALLBACK_PROMPT = """I'll arrange for a human representative to follow up with you.

You can expect a response within the next 24 hours (or the next business day). If your request is urgent, please use the direct contact option for faster assistance."""

HANDOFF_SYSTEM_PROMPT = """
You are responsible for selecting the correct human handoff mode and generating a structured handoff message for a human agent.

Your output is NOT a message to the user.
It is an internal message intended for a human representative to understand the situation and take action.

---

Step 1: Determine the user's intent and select ONE mode:

1. direct_contact
- Use when the user prefers to contact a human themselves.
- No handoff is required.
- Message should be minimal or empty.

2. agent_escalation
- Use when the user wants immediate human assistance or escalation.
- The message should summarize the user's request and the **reason** for escalation based on conversation history.

3. scheduled_callback
- Use when the user wants a human to reach out later OR when a callback is required.
- Include extracted contact details if available.
- If contact info is missing, clearly indicate what is missing in the message.

---

Step 2: Extract Contact Information

- Extract any phone number(s) mentioned by the user.
- Extract any email address(es) mentioned by the user.
- Preserve the original format exactly as provided.
- If multiple numbers or emails are present, include all of them.
- If no contact details are provided, explicitly list them as missing.
- Do NOT ask the user for missing details — only report their absence.

---

Message Guidelines:

- The message must be written for a HUMAN AGENT, not the user.
- Be concise, structured, and informative.
- Include:
  - User intent
  - Key issue or request (reason for handoff)
  - Relevant context from conversation history
  - Extracted contact details (if any)
  - Missing information (if any)
  - Username (if available)
- If the user did not specify a reason for requesting a human, default to:
  "User requests to speak with a human."
- Do NOT mention that the user wants to connect to a human as the reason; instead, summarize the underlying context.
- Do NOT include greetings, conversational filler, or system explanations.

---

Username: {username}

---

Output Format (strict JSON):

{{
  "mode": "direct_contact | agent_escalation | scheduled_callback",
  "message": "<internal handoff summary including reason/context>",
  "contact": {{
    "phone_numbers": ["<extracted or empty>"],
    "emails": ["<extracted or empty>"],
    "missing": ["phone_number" | "email" | null]
  }}
}}
"""


class HandoffInteractAction(InteractAction):
    """Interact action that hands users to human support with LLM-chosen handling.

    Modes:
    - direct_contact — user sees support contact details; no outbound notification.
    - agent_escalation — user sees an escalation message; internal summary sent via WhatsApp when contact info exists.
    - scheduled_callback — user sees a callback confirmation; internal summary sent via WhatsApp when contact info exists.
    """

    description: str = (
        "Detects human-support intent and routes to direct contact, WhatsApp escalation, "
        "or scheduled callback using conversation context and extracted contact details."
    )

    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User wants to speak to a human.",
            "User requests a human agent.",
            "User asks to be connected to support.",
            "User does not want to continue with the AI.",
            "User expresses frustration and asks for a real person.",
            "User asks for contact information for support.",
            "User wants to escalate the conversation.",
            "User requests a callback from a human.",
            "User asks for phone, email, or WhatsApp contact.",
            "User says they need further assistance from a human.",
            "User indicates the issue is not resolved and wants escalation.",
        ],
        description="Anchor statements for InteractRouter (handoff intent detection).",
    )

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Model action type",
    )
    model: str = attribute(default="gpt-4o-mini", description="Model name")
    model_temperature: float = attribute(
        default=0.1, description="Sampling temperature"
    )
    model_max_tokens: int = attribute(default=8192, description="Max tokens")
    use_history: bool = attribute(default=True, description="Use history")
    history_limit: int = attribute(default=6, description="History limit")
    max_statement_length: int = attribute(
        default=400, description="Max statement length"
    )

    direct_contact_prompt: str = attribute(
        default=DIRECT_CONTACT_PROMPT,
        description="Prompt for direct contact",
    )

    agent_escalation_prompt: str = attribute(
        default=AGENT_ESCALATION_PROMPT,
        description="Prompt for agent escalation",
    )

    scheduled_callback_prompt: str = attribute(
        default=SCHEDULED_CALLBACK_PROMPT,
        description="Prompt for scheduled callback",
    )

    handoff_system_prompt: str = attribute(
        default=HANDOFF_SYSTEM_PROMPT,
        description="Prompt for handoff",
    )

    handoff_mode: str = attribute(
        default="direct_contact",
        description="direct_contact, agent_escalation, scheduled_callback",
    )

    handoff_number: str = attribute(
        default="5926431530",
        description="Handoff number",
    )

    ########################################################################################
    # CUSTOM FUNCTIONS
    ########################################################################################

    async def _call_model(
        self,
        user_prompt: str,
        system_prompt: str,
        json_response: bool = False,
        use_history: bool = False,
        interaction: Optional[Interaction] = None,
        history_limit: int = 3,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        max_statement_length: Optional[int] = 100,
    ) -> Any:
        """Call the configured language model, optionally with conversation history.

        Parameters:
            user_prompt: Primary user-side prompt text.
            system_prompt: System instructions.
            json_response: If True, parse the model output as a JSON object.
            use_history: If True, load history via PersonaAction.
            interaction: Current interaction (needed when ``use_history`` is True).
            history_limit: Number of past turns to include.
            with_utterance: Include user utterances in history.
            with_response: Include assistant responses in history.
            with_interpretation: Include interpretation snippets in history.
            with_event: Include events in history.
            max_statement_length: Truncate long lines when building history.

        Returns:
            - If json_response=True: Parsed JSON dict on success
            - If json_response=False: Raw string response
            - False if model action unavailable
            - None if exception occurs

        Example:
            # Text response
            response = await self._call_model(
                user_prompt="What is Python?",
                system_prompt="You are a programming expert."
            )

            # JSON response
            data = await self._call_model(
                user_prompt="List 3 Python frameworks",
                system_prompt="Return JSON",
                json_response=True
            )
        """

        conversation_history = None
        if use_history:
            persona_action = await self.get_action(PersonaAction)
            if persona_action:
                conversation_history = await persona_action._get_conversation_history(
                    interaction,
                    history_limit,
                    with_utterance=with_utterance,
                    with_response=with_response,
                    with_interpretation=with_interpretation,
                    with_event=with_event,
                    max_statement_length=max_statement_length,
                )

                # for reply coherence
                if (
                    with_interpretation
                    and not with_response
                    and interaction.response
                    and conversation_history is not None
                ):
                    conversation_history.append(
                        {
                            "role": "assistant",
                            "content": interaction.response,
                        }
                    )

        try:
            model_action = await self.get_model_action()
            if not model_action:
                return False
            if json_response:
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=self.model,
                    temperature=self.model_temperature,
                    max_tokens=self.model_max_tokens,
                    response_format={"type": "json_object"},
                    history=conversation_history,
                    calling_action_name=self.get_class_name(),
                )

                json_match = re.search(
                    r"```(?:json)?\s*({.*?})\s*```", result_str, re.DOTALL
                )
                if json_match:
                    result_str = json_match.group(1)
                elif result_str.strip().startswith("{"):
                    result_str = result_str.strip()
                else:
                    json_match = re.search(r"{.*}", result_str, re.DOTALL)
                    result_str = (
                        json_match.group(0) if json_match else result_str.strip()
                    )

                return json.loads(result_str)
            else:
                return await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=self.model,
                    temperature=self.model_temperature,
                    max_tokens=self.model_max_tokens,
                    history=conversation_history,
                    calling_action_name=self.get_class_name(),
                )
        except Exception as e:
            logger.error(f"Error in LLM helper: {e}")
            return None

    ########################################################################################
    # CORE FUNCTIONS
    ########################################################################################

    async def execute(self, visitor: InteractWalker) -> None:
        """Execute the handoff action."""

        # selecting the handoff mode and their message
        user = await visitor.interaction.get_user()
        username = "Unknown"
        if user:
            username = user.get_display_name()
        user_prompt = visitor.utterance 
        system_prompt = self.handoff_system_prompt.format(username=username)

        handoff_result = await self._call_model(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            json_response=True,
            use_history=self.use_history,
            history_limit=self.history_limit,
            interaction=visitor.interaction,
            with_utterance=True,
            with_response=True,
            with_interpretation=False,
            with_event=True,
            max_statement_length=self.max_statement_length,
        )

        message = handoff_result.get("message", "")
        handoff_mode = handoff_result.get("mode", "")
        contact_info = handoff_result.get("contact", {})
        phone_numbers = contact_info.get("phone_numbers", [])
        emails = contact_info.get("emails", [])
        phone = self.handoff_number

        whatsapp_action = await self.get_action(WhatsAppAction)
        whatsapp_api = (
            await whatsapp_action.api() if whatsapp_action is not None else None
        )

        # Handle the handoff mode
        if handoff_mode == "direct_contact":
            visitor.interaction.directives = [
                {
                    "action_name": self.get_class_name(),
                    "content": self.direct_contact_prompt,
                    "executed": False,
                }
            ]
        elif handoff_mode == "agent_escalation":

            if not phone_numbers and not emails:
                # ask for contact info if missing
                visitor.interaction.directives = [
                    {
                        "action_name": self.get_class_name(),
                        "content": f"Please provide your contact information if you would like a callback.",
                        "executed": False,
                    }
                ]
            else:
                visitor.interaction.directives = [
                    {
                        "action_name": self.get_class_name(),
                        "content": self.agent_escalation_prompt,
                        "executed": False,
                    }
                ]
                if whatsapp_api:
                    await whatsapp_api.send_message(phone=phone, message=message)
        elif handoff_mode == "scheduled_callback":
            if not phone_numbers and not emails:
                # ask for contact info if missing
                visitor.interaction.directives = [
                    {
                        "action_name": self.get_class_name(),
                        "content": f"Please provide your contact information if you would like a callback.",
                        "executed": False,
                    }
                ]
            else:
                visitor.interaction.directives = [
                    {
                        "action_name": self.get_class_name(),
                        "content": self.scheduled_callback_prompt,
                        "executed": False,
                    }
                ]

                if whatsapp_api:
                    await whatsapp_api.send_message(phone=phone, message=message)

        return
