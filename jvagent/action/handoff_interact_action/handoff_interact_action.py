"""Handoff interact action."""

import logging
from typing import List

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.utils.call_model import call_model

logger = logging.getLogger(__name__)

DIRECT_CONTACT_PROMPT = """You can reach a human representative directly using the contact details below:

Email: {handoff_email}
Phone / WhatsApp: {handoff_phone}
Office Hours: {handoff_hours}

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
        description="Anchor statements for Orchestrator tool surfacing (handoff intent).",
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
        default="",
        description=(
            "Phone / WhatsApp number for direct contact. Configure via "
            "agent.yaml ``context.handoff_number`` (or pin to an env var "
            "with ``${HANDOFF_NUMBER}``). Empty string disables the phone "
            "line in the DIRECT_CONTACT_PROMPT template."
        ),
    )

    handoff_email: str = attribute(
        default="",
        description=(
            "Email for direct contact. Configure via agent.yaml "
            "``context.handoff_email`` or ``${HANDOFF_EMAIL}``. AUDIT-actions"
            " (Wave D removed the previous hardcoded ``support@company.com``)."
        ),
    )

    handoff_hours: str = attribute(
        default="Mon-Fri, 9:00 AM - 5:00 PM",
        description="Office hours phrase rendered into DIRECT_CONTACT_PROMPT.",
    )

    handoff_notify_action_type: str = attribute(
        default="WhatsAppAction",
        description=(
            "Action class name used to notify staff on escalation/callback "
            "(must expose ``api()`` with ``send_message``)."
        ),
    )

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

        handoff_result = await call_model(
            self,
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
            model=self.model,
            temperature=self.model_temperature,
            max_tokens=self.model_max_tokens,
        )

        if not isinstance(handoff_result, dict):
            logger.error(
                "Handoff model call failed or returned non-JSON: %r", handoff_result
            )
            return

        message = handoff_result.get("message", "")
        handoff_mode = handoff_result.get("mode", "")
        contact_info = handoff_result.get("contact", {})
        phone_numbers = contact_info.get("phone_numbers", [])
        emails = contact_info.get("emails", [])
        phone = self.handoff_number

        agent = await self.get_agent()
        notify_action = (
            await agent.get_action_by_type(self.handoff_notify_action_type)
            if agent
            else None
        )
        notify_api = await notify_action.api() if notify_action is not None else None

        # Handle the handoff mode
        if handoff_mode == "direct_contact":
            # Render contact placeholders into the operator-configurable
            # template. When a field is blank, drop the line so we never
            # show ``Email: `` / ``Phone: ``. Falls back to the literal
            # template when there are no `{...}` placeholders (legacy
            # override). AUDIT-actions D.1 (Wave D).
            try:
                rendered = self.direct_contact_prompt.format(
                    handoff_email=self.handoff_email,
                    handoff_phone=self.handoff_number,
                    handoff_hours=self.handoff_hours,
                )
            except (KeyError, IndexError):
                rendered = self.direct_contact_prompt
            # Trim blank-field lines so an empty handoff_email/number does
            # not render as ``Email: ``.
            cleaned_lines = []
            for line in rendered.split("\n"):
                stripped = line.strip()
                if stripped.endswith(":") and (
                    stripped.lower().startswith("email:")
                    or stripped.lower().startswith("phone")
                ):
                    continue
                cleaned_lines.append(line)
            rendered = "\n".join(cleaned_lines)
            visitor.interaction.directives = [
                {
                    "action_name": self.get_class_name(),
                    "content": rendered,
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
                if notify_api:
                    await notify_api.send_message(phone=phone, message=message)
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

                if notify_api:
                    await notify_api.send_message(phone=phone, message=message)

        return
