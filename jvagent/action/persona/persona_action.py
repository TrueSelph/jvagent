"""PersonaAction base class - simplified tool-based action.

This module provides a simplified PersonaAction class that serves as a tool-based
action for applying agent prompts with configurable parameters.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jvspatial.api.exceptions import ValidationError
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.interact.utils import (
    build_prompt_for_vision,
    generate_image_interpretation,
)
from jvagent.action.persona.prompts import (
    ACTIVE_TASKS_SECTION_PROMPT,
    CANNED_LEAD_IN_CONTEXT_PROMPT,
    CHANNEL_OVERRIDE_PREAMBLE,
    CONTINUATION_GUIDANCE_PROMPT,
    DIRECTIVE_COMPLIANCE_CHECK_PROMPT,
    DIRECTIVES_SECTION_PROMPT,
    EXAMPLES_SECTION_PROMPT,
    INTERPRETATION_INSIGHTS_PROMPT,
    NO_DIRECTIVES_SUB_PROMPT,
    PARAMETERS_SUB_PROMPT,
    RESPONSE_LENGTH_PROMPT,
    RESPONSE_PROTOCOL_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    VISION_IMAGE_INSTRUCTION,
    format_conditional_section,
    format_parameter,
    get_channel_directive,
)
from jvagent.core.channel import normalize_channel
from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class PersonaAction(Action):
    """Simplified tool-based action for applying agent prompts.

    PersonaAction is a tool-based action that applies the main agent prompt
    with configurable parameters. It provides a simple interface for generating
    responses using language models.

    Attributes:
        system_prompt: System prompt template for the agent (default property)
        parameters: Standard collection of configurable parameters to apply when executing the prompt
        model_action_type: Entity type of the LanguageModelAction (e.g., "OpenAILanguageModelAction")
        model: Default model name
        model_temperature: Temperature for LLM generation
        model_max_tokens: Max tokens for LLM generation
        vision_model_action_type: Optional alternate LanguageModelAction for the vision-analysis pass (stored interpretation)
        vision_model: Optional model id override for that pass only
        vision_model_temperature: Optional temperature for that pass only
        vision_model_max_tokens: Optional max_tokens for that pass only
        persona_name: Agent display name (for prompt formatting)
        persona_description: Detailed agent description (for prompt formatting)
        persona_capabilities: List of agent capabilities (for prompt formatting)
    """

    # Persona attributes (for prompt formatting if using default template)
    persona_name: str = attribute(default="Agent", description="Agent display name")
    persona_description: str = attribute(
        default="You are friendly and helpful",
        description="Detailed agent description",
    )
    expression_examples: List[Dict[str, str]] = attribute(
        default_factory=list,
        description="List of user/assistant example pairs for few-shot learning",
    )
    persona_capabilities: List[str] = attribute(
        default_factory=list, description="List of agent capabilities"
    )
    phonetic_substitutions: Dict[str, str] = attribute(
        default_factory=dict,
        description="Map of original -> phonetic replacement for voice channel (e.g. essequibo: esseequibbo)",
    )

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use (e.g., OpenAILanguageModelAction)",
    )
    model: str = attribute(default="gpt-4o", description="Default model name")
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )
    voice_max_tokens: int = attribute(
        default=150,
        description="Max tokens for voice channel (~100 words); used when channel=voice",
    )

    # Vision model configuration (optional; used only for the behind-the-scenes image
    # analysis pass that stores a description on Interaction.image_interpretation so
    # follow-up questions work without re-attaching the image). When these are unset
    # the primary model_action / model is used instead, preserving existing behavior.
    vision_model_action_type: str = attribute(
        default="",
        description=(
            "LanguageModelAction entity type to use for analyzing attached images "
            "(e.g. 'OpenAILanguageModelAction'). When empty the primary model_action_type "
            "is used. Set this to route vision analysis to a dedicated vision/omni provider "
            "while keeping a different model for normal text replies."
        ),
    )
    vision_model: str = attribute(
        default="",
        description=(
            "Model identifier for the vision-analysis pass only "
            "(e.g. 'gpt-4o', 'claude-opus-4-7'). When empty the resolved LanguageModelAction's "
            "own default model is used."
        ),
    )
    vision_model_temperature: Optional[float] = attribute(
        default=None,
        description=(
            "Temperature override for the vision-analysis pass. "
            "When None the provider's default is used."
        ),
    )
    vision_model_max_tokens: Optional[int] = attribute(
        default=None,
        description=(
            "Max-tokens override for the vision-analysis pass. "
            "When None the provider's default is used."
        ),
    )

    # Response length limits (words, prompt-based; model generates concise output)
    response_limit: int = attribute(
        default=10000,
        description="Maximum words for persona replies on non-voice channels (0 = disabled).",
    )

    channel_response_limits: dict = attribute(
        default={
            "email": 10000,
            "sms": 160,
            "facebook": 500,
            "whatsapp": 500,
            "web": 500,  # webchat
        },
        description="Maximum words for persona replies on different channels.",
    )

    voice_response_limit: int = attribute(
        default=60,
        description="Maximum words for voice channel (TTS). Used when channel=voice.",
    )

    remind_on_active_tasks: bool = attribute(
        default=True,
        description="When True and active tasks require user intervention, include reminder to return if conversation strays",
    )

    # System prompt (default property)
    system_prompt: str = attribute(
        default=SYSTEM_PROMPT_TEMPLATE,
        description="System prompt template for the agent",
    )

    # Structured output configuration (optional, default False for backward compatibility)
    use_structured_output: bool = attribute(
        default=False,
        description="Enable structured JSON output with insights, context evaluation, and revisions (default: False for backward compatibility)",
    )

    # Standard collection of configurable parameters
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "condition": "When providing a response",
                "response": "End with the answer. Avoid closers such as 'feel free to ask', 'let me know if you have questions', or 'anything else I can help with?'—and similar invitations to ask more. Be direct and conclusive.",
            },
            {
                "condition": "User asks about OpenAI or your origin.",
                "response": "Deny any association with OpenAI even when asked directly.",
            },
            {
                "condition": "User asks for your identity.",
                "response": "Refer to yourself only by your name. Never say you are an AI language model.",
            },
            {
                "condition": "User request is outside your role or ability.",
                "response": "Admit that the request is outside your role or ability; Do not give inaccurate answers and avoid giving details not explicitly stated in this prompt.",
            },
            {
                "condition": "User requests information that you have already provided.",
                "response": "Make the user aware that you have already provided the information.",
            },
            {
                "condition": "The conversation seems repetitive.",
                "response": "Bring the circular conversation to the user's attention.",
            },
            {
                "condition": "You are likely to mention how recent your knowledge cutoff is",
                "response": "Do not give explicit details about your knowledge cutoff. Instead, respond with a general statement like 'I have access to a wealth of information but I admit I do not know everything",
            },
        ],
        description="Standard collection of configurable parameters to apply when executing the prompt",
    )

    async def on_register(self) -> None:
        """Initialize when action is registered."""
        await super().on_register()
        logger.info(f"PersonaAction '{self.label}' registered")

    async def respond(
        self,
        interaction: Interaction,
        visitor: Optional[Any] = None,
        use_history: bool = True,
        history_limit: int = 4,
        with_utterance: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
        transient: bool = False,
    ) -> str:
        """Main utility method for generating a response.

        Accepts the active interaction object with the utterance, injected directives
        and parameters, then makes the language model call with the composed prompt
        and returns the generated response.

        When visitor is provided and has response_bus and session_id, the generated
        response is delivered via the response bus (streaming: by the model; non-streaming:
        by a single publish) and interaction.response is updated accordingly.
        When no such visitor is provided, only interaction.response is set and saved.

        Args:
            interaction: The active interaction object containing:
                - utterance: User's input text
                - directives: Injected directives from other actions
                - parameters: Applicable parameters for this interaction
            visitor: Optional InteractWalker for streaming support
            use_history: Whether to include conversation history (default: True)
            history_limit: Number of past interactions to include in history (default: 4)
            with_utterance: Whether to include the user's utterance in the prompt (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: True)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None, no truncation)
            transient: If True, skip appending response to interaction.response. Use for temporary
                messages like canned responses or typing indicators (default: False)

        Returns:
            Generated response string from the language model

        Raises:
            ValidationError: If there are no directives or parameters to apply for this turn.
            RuntimeError: If PersonaAction is not attached to an agent or model action not found
        """
        # Get model action (required=True raises error if not found)
        model_action = await self.get_model_action(required=True)

        # Add all persona parameters to the interaction (like interact actions do)
        # This allows accurate recording of persona-level parameters applied to each response
        # Duplicate prevention is handled by interaction.add_parameters()
        persona_action_name = self.get_class_name()
        persona_parameters_to_add = (
            list(self.parameters) if self.parameters is not None else []
        )

        if persona_parameters_to_add:
            # Only save if parameters were actually added (not duplicates)
            if interaction.add_parameters(
                persona_parameters_to_add, persona_action_name
            ):
                await interaction.save()

        # Get unexecuted directives and parameters (now includes persona parameters)
        applicable_directives = interaction.get_unexecuted_directives()
        applicable_parameters = interaction.get_unexecuted_parameters()

        # Check for existing response to avoid repetition (multi-call awareness)
        if interaction.response:
            if not applicable_directives and not applicable_parameters:
                logger.debug(
                    f"PersonaAction.respond: Skipping - interaction already has response "
                    f"({len(interaction.response)} chars) and no new directives/parameters."
                )
                return interaction.response

        # Validate that there are directives and/or parameters to proceed
        # applicable_parameters now includes persona parameters from the interaction
        if not (applicable_directives or applicable_parameters):
            raise ValidationError(
                message=(
                    "No persona directives or parameters are available for this turn. "
                    "Ensure the interaction has unexecuted directives or parameters, "
                    "or configure PersonaAction parameters."
                ),
                details={"reason": "no_directives_or_parameters"},
            )

        # Generate response using direct prompt approach (pass model_action to avoid second lookup)
        return await self._generate_response(
            interaction,
            visitor,
            applicable_directives,
            applicable_parameters,
            use_history,
            history_limit,
            with_utterance,
            with_interpretation,
            with_event,
            with_response,
            max_statement_length,
            transient,
            model_action=model_action,
        )

    async def respond_slim(
        self,
        interaction: Interaction,
        visitor: Optional[Any] = None,
        *,
        prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        extra_system: Optional[str] = None,
        transient: bool = False,
    ) -> Optional[str]:
        """Single LM turn with ``system`` = ``persona_description`` only (no full compose).

        Skips ``_compose_prompt``, directives, and persona parameter injection. Uses the
        same model, streaming / ``_pipe_response``, voice formatting, and optional
        image interpretation as the main respond path where applicable.

        ``extra_system`` is appended to ``persona_description`` so callers (notably
        the cockpit's converse fast-path) can inject a brief instruction (for
        example, "Reply briefly in character; match the user's tone.") without
        paying the full ``respond()`` compose / parameter-injection cost.

        Args:
            interaction: Active interaction (utterance used if ``prompt`` is omitted).
            visitor: Optional walker for bus / streaming.
            prompt: User-facing prompt text; defaults to ``interaction.utterance``.
            history: Optional formatted history list for ``generate`` (default none).
            extra_system: Optional additional system instruction appended after
                ``persona_description``. Single string; whitespace is trimmed.
            transient: Passed through to ``generate`` / ``_pipe_response``.

        Returns:
            Generated text, or None if generation returned empty.

        Raises:
            ValidationError: If ``persona_description`` is empty.
            RuntimeError: If no language model action is available.
        """
        system = (self.persona_description or "").strip()
        if not system:
            raise ValidationError(
                message="PersonaAction.respond_slim requires non-empty persona_description.",
                details={"reason": "empty_persona_description"},
            )

        # Bake current date / time / timezone + user identity into the system
        # prompt so the model has authoritative anchors without resorting to
        # its training-cutoff date or hallucinating a name. The full
        # ``respond()`` path injects these via the persona prompt template;
        # ``respond_slim`` callers (notably the cockpit converse fast-path)
        # need the same context inline.
        datetime_block = await self._render_datetime_context_block()
        if datetime_block:
            system = f"{system}\n\n{datetime_block}"

        user_block = await self._render_user_context_block(interaction)
        if user_block:
            system = f"{system}\n\n{user_block}"

        if extra_system and extra_system.strip():
            system = f"{system}\n\n{extra_system.strip()}"

        model_action = await self.get_model_action(required=True)

        if visitor:
            data = getattr(visitor, "data", None) or {}
            image_urls = data.get("image_urls") or []
            if image_urls and data.get("image_interpretation") is not False:
                try:
                    vision_pass_model_action = (
                        await self._get_vision_model_action() or model_action
                    )
                    vision_pass_kwargs: dict = {}
                    if self.vision_model:
                        vision_pass_kwargs["model"] = self.vision_model
                    if self.vision_model_temperature is not None:
                        vision_pass_kwargs["temperature"] = (
                            self.vision_model_temperature
                        )
                    if self.vision_model_max_tokens is not None:
                        vision_pass_kwargs["max_tokens"] = self.vision_model_max_tokens
                    interpretation = await generate_image_interpretation(
                        image_urls, vision_pass_model_action, **vision_pass_kwargs
                    )
                    if interpretation:
                        interaction.image_interpretation = interpretation
                        await interaction.save()
                except Exception as e:
                    logger.warning(
                        f"PersonaAction.respond_slim: Failed to generate image interpretation: {e}"
                    )

        prompt_text = prompt if prompt is not None else (interaction.utterance or "")
        use_voice = self._use_voice_formatting(interaction, visitor)
        if use_voice and prompt_text:
            prompt_text = (
                f"{prompt_text}\n\n[VOICE: Plain text only. Max {self.voice_response_limit} "
                "words. No markdown, lists, or **bold**.]"
            )

        prompt_text = build_prompt_for_vision(prompt_text, visitor, model_action)

        streaming = bool(
            visitor
            and getattr(visitor, "stream", False)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        response_format = (
            {"type": "json_object"} if self.use_structured_output else None
        )
        max_tokens = self.voice_max_tokens if use_voice else self.model_max_tokens

        response = await model_action.generate(
            prompt=prompt_text or " ",
            stream=streaming,
            system=system,
            history=list(history or []),
            calling_action_name=self.get_class_name(),
            model=self.model,
            temperature=self.model_temperature,
            max_tokens=max_tokens,
            response_bus=response_bus if streaming else None,
            interaction=interaction,
            response_format=response_format,
            transient=transient,
        )

        if self.use_structured_output and response:
            parsed_response = self._parse_structured_output(response)
            if parsed_response:
                response = parsed_response

        if use_voice and response:
            response = self._sanitize_voice_response(response)

        if response and response.strip():
            await self._pipe_response(
                response, interaction, visitor, streaming, transient
            )
            interaction.record_action_execution("PersonaAction")

        return response

    async def _get_user_display_name(self, interaction: Interaction) -> str:
        """Resolve a friendly user name for prompt personalization."""
        try:
            user = await interaction.get_user()
            if user:
                return user.get_display_name()
        except Exception as e:
            logger.debug(f"PersonaAction: failed to resolve user display name: {e}")
        return "user"

    async def _render_user_context_block(self, interaction: Interaction) -> str:
        """Build a user-identity block for the system prompt.

        Resolves the caller's preferred name from the User node
        (``display_name`` then ``name``). When the name is unknown the
        block tells the model to ask politely rather than guess. Empty
        string when there's no interaction context to read from.
        """
        if interaction is None:
            return ""
        try:
            user = await interaction.get_user()
        except Exception as exc:
            logger.debug("PersonaAction: failed to resolve user node: %s", exc)
            user = None

        display_name = ""
        canonical_name = ""
        if user is not None:
            try:
                display_name = (getattr(user, "display_name", "") or "").strip()
            except Exception:
                display_name = ""
            try:
                canonical_name = (getattr(user, "name", "") or "").strip()
            except Exception:
                canonical_name = ""

        chosen = display_name or canonical_name
        if chosen:
            lines = [
                "### USER IDENTITY (your authoritative reference for who you are speaking to)",
                f"Preferred name: {chosen}",
            ]
            if display_name and canonical_name and display_name != canonical_name:
                lines.append(f"Canonical name: {canonical_name}")
            lines.append(
                "Use this name when addressing the user. Do not invent or "
                "alter it. If the user offers a different name, persist the "
                "update via ``memory_update_user_model`` (key=``name``)."
            )
            return "\n".join(lines)

        return (
            "### USER IDENTITY\n"
            "No name is on file for this user. If a greeting needs a name, "
            "ask politely how they would like to be addressed; persist the "
            "answer via ``memory_update_user_model`` (key=``name``). "
            "Never invent a name."
        )

    async def _render_datetime_context_block(self) -> str:
        """Build a temporal-anchor block for the system prompt.

        Returns the same multi-format datetime view exposed by the cockpit
        ``get_current_datetime`` tool so the model treats both surfaces as
        the single source of truth. Empty string on resolution failure —
        the caller's prompt stays valid; the model will fall back to the
        tool when temporal precision matters.
        """
        try:
            from datetime import datetime, timezone

            from jvagent.core.app import App

            app = await App.get()
            now = await app.now() if app is not None else None
            if not isinstance(now, datetime):
                now = datetime.now(timezone.utc)
        except Exception as exc:
            logger.debug("PersonaAction: failed to resolve current datetime: %s", exc)
            return ""

        tz_label = getattr(now.tzinfo, "key", None) or (
            str(now.tzinfo) if now.tzinfo else "naive"
        )
        epoch = int(now.timestamp()) if now.tzinfo is not None else 0
        return (
            "### CURRENT DATE / TIME (your authoritative temporal anchor)\n"
            f"ISO 8601: {now.isoformat()}\n"
            f"Date: {now.strftime('%A, %B %d, %Y')}\n"
            f"Time: {now.strftime('%H:%M:%S')}\n"
            f"Timezone: {tz_label}\n"
            f"Unix epoch (seconds): {epoch}\n"
            "Use these values for any 'today' / 'now' / scheduling reference. "
            "Never substitute your training-cutoff date. For alternate "
            "timezones or post-anchor precision, the agent has a "
            "``get_current_datetime`` tool available."
        )

    async def _get_vision_model_action(self) -> Optional[Any]:
        """Resolve the optional LanguageModelAction for the vision-analysis pass.

        Returns the action identified by `vision_model_action_type` when configured,
        falling back to None (caller should then use the primary model_action) when
        the attribute is empty or the action cannot be found.
        """
        from jvagent.action.model.language.base import LanguageModelAction

        action_type = getattr(self, "vision_model_action_type", None)
        if not action_type:
            return None
        try:
            action = await self.get_action(action_type)
            if action and isinstance(action, LanguageModelAction):
                return action
            logger.warning(
                f"PersonaAction: vision_model_action_type '{action_type}' not found or not a "
                f"LanguageModelAction — falling back to primary model_action for vision analysis."
            )
        except Exception as e:
            logger.warning(
                f"PersonaAction: failed to resolve vision_model_action_type '{action_type}': {e} "
                f"— falling back to primary model_action for vision analysis."
            )
        return None

    def _sanitize_voice_response(
        self, response: str, max_words: Optional[int] = None
    ) -> str:
        """Sanitize response for voice/TTS: strip markdown, collapse whitespace, truncate.

        Fallback when the model disobeys voice format rules.
        Uses voice_response_limit when max_words is None.
        """
        if not response or not response.strip():
            return response
        limit = max_words if max_words is not None else self.voice_response_limit
        text = response.strip()
        # Strip markdown: **bold** -> bold, *italic* -> italic
        text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]*)\*", r"\1", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"^---+?\s*$", "", text, flags=re.MULTILINE)
        # Strip [text](url) -> text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Remove numbered/bullet list prefixes (1. 2. - *)
        text = re.sub(r"^\s*\d+\.\s*", " ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[-*]\s+", " ", text, flags=re.MULTILINE)
        # Collapse double newlines and extra whitespace
        text = re.sub(r"\n\n+", ". ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Truncate to limit
        words = text.split()
        if len(words) <= limit:
            return text
        truncated = words[:limit]
        # Prefer sentence boundary
        last_sentence_end = -1
        for i in range(len(truncated) - 1, -1, -1):
            w = truncated[i]
            if w and w[-1] in ".!?":
                last_sentence_end = i
                break
        if last_sentence_end > limit // 2:
            truncated = truncated[: last_sentence_end + 1]
        return " ".join(truncated).strip()

    def _non_voice_response_word_limit(self, interaction: Interaction) -> int:
        """Word cap for non-voice replies: per-channel override then ``response_limit``."""
        ch = normalize_channel(getattr(interaction, "channel", None))
        limits = self.channel_response_limits
        if isinstance(limits, dict) and ch in limits:
            raw = limits[ch]
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        return self.response_limit if self.response_limit > 0 else 0

    def _use_voice_formatting(
        self, interaction: Interaction, visitor: Optional[Any]
    ) -> bool:
        """True when response will be spoken (voice channel or TTS via adapter)."""
        if interaction.channel == "voice":
            return True
        if not visitor:
            return False
        data = getattr(visitor, "data", None) or {}
        return bool(data.get("respond_with_voice"))

    async def _pipe_response(
        self,
        response: str,
        interaction: Interaction,
        visitor: Optional[Any],
        streaming: bool,
        transient: bool = False,
    ) -> None:
        """Persist and optionally publish response to the response bus.

        When transient=True, the response is published but NOT appended to
        interaction.response. This is useful for temporary messages like
        canned responses or typing indicators.

        When there is no bus: persist to interaction.response (append if continuing)
        and save. When there is a bus and streaming: no-op (model already published).
        When there is a bus and non-streaming: PersonaAction is the sole publisher;
        call publish once.

        Args:
            response: The response content
            interaction: The interaction object
            visitor: Optional InteractWalker
            streaming: Whether streaming mode is active
            transient: If True, skip appending to interaction.response
        """
        if not response:
            return
        response_bus = getattr(visitor, "response_bus", None) if visitor else None
        has_bus = bool(
            response_bus and visitor and getattr(visitor, "session_id", None)
        )

        if not has_bus:
            if not transient:
                current_response = interaction.response or ""
                response_changed = False
                # Continuing (append with separator) vs replace/set
                if (
                    current_response
                    and current_response.strip()
                    and current_response != response
                ):
                    response_changed = interaction.set_response(
                        f"{current_response}\n\n{response}"
                    )
                else:
                    response_changed = interaction.set_response(response)
                if response_changed:
                    await interaction.save()
            return

        if streaming:
            return

        channel = getattr(visitor, "channel", "default")
        if not channel or channel == "default":
            logger.warning(
                f"{self.get_class_name()}: Visitor channel not set or is 'default', "
                f"using channel='{channel}'. This may prevent channel adapters from receiving messages."
            )
        visitor_data = getattr(visitor, "data", None) or {} if visitor else {}
        metadata = dict(visitor_data)
        await response_bus.publish(
            session_id=visitor.session_id,
            content=response,
            channel=channel,
            stream=False,
            metadata=metadata,
            interaction_id=interaction.id,
            interaction=interaction,
            user_id=interaction.user_id if hasattr(interaction, "user_id") else None,
            streaming_complete=True,
            transient=transient,
        )

    def _parse_structured_output(self, response: str) -> Optional[str]:
        """Parse structured JSON output to extract final message content.

        When use_structured_output is enabled, the LLM returns a JSON response with:
        - insights: List of gathered insights
        - context_evaluation: Structured context assessment
        - revisions: List of revision attempts with the final message content

        This method extracts the final message content from the last revision.

        Args:
            response: The JSON string response from the LLM

        Returns:
            Final message content string, or None if parsing fails
        """
        try:
            # Try to parse JSON
            if isinstance(response, str):
                # Clean up potential markdown code blocks
                response_clean = response.strip()
                if response_clean.startswith("```json"):
                    response_clean = response_clean[7:]
                if response_clean.startswith("```"):
                    response_clean = response_clean[3:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()

                data = json.loads(response_clean)
            else:
                data = response

            # Extract final message from revisions
            if "revisions" in data and data["revisions"]:
                # Get the last revision's content
                last_revision = data["revisions"][-1]
                if "content" in last_revision:
                    final_content = last_revision["content"]

                    # Log insights and context evaluation if available (for debugging)
                    if "insights" in data:
                        logger.debug(f"PersonaAction insights: {data['insights']}")
                    if "context_evaluation" in data:
                        logger.debug(
                            f"PersonaAction context evaluation: {data['context_evaluation']}"
                        )

                    return final_content

            # Fallback: if no revisions, return the whole response
            logger.warning(
                "PersonaAction: Structured output enabled but no revisions found in response"
            )
            return response if isinstance(response, str) else json.dumps(response)

        except json.JSONDecodeError as e:
            logger.warning(
                f"PersonaAction: Failed to parse structured JSON output: {e}. Returning raw response."
            )
            return response if isinstance(response, str) else str(response)
        except Exception as e:
            logger.error(
                f"PersonaAction: Error parsing structured output: {e}", exc_info=True
            )
            return response if isinstance(response, str) else str(response)

    async def _compose_prompt(
        self,
        interaction: Interaction,
        applicable_directives: List[Dict[str, Any]],
        applicable_parameters: List[Dict[str, Any]],
        visitor: Optional[Any] = None,
    ) -> str:
        """Compose the system prompt using the consolidated template.

        Args:
            interaction: The active interaction object
            applicable_directives: List of unexecuted directives
            applicable_parameters: List of unexecuted parameters
            visitor: Optional InteractWalker (for respond_with_voice check)

        Returns:
            Composed system prompt string
        """
        now = await self.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")
        app = await self.get_app()
        timezone_str = (app.timezone or "UTC") if app else "UTC"

        # Canned lead-in + continuation (multi-call / transient canned before full reply)
        from jvagent.memory.conversation import Conversation

        canned_raw = (getattr(interaction, "canned_response", None) or "").strip()
        response_raw = (interaction.response or "").strip()
        canned_absorbed_in_response = bool(
            canned_raw and response_raw and response_raw.startswith(canned_raw)
        )

        canned_lead_in_section = ""
        continuation_guidance = ""

        if response_raw:
            user_utterance = await Conversation.truncate_statement(
                interaction.utterance or "", max_length=500, interaction=interaction
            )
            if canned_raw and not canned_absorbed_in_response:
                canned_trunc = await Conversation.truncate_statement(
                    canned_raw,
                    max_length=500,
                    interaction=interaction,
                )
                prev_trunc = await Conversation.truncate_statement(
                    response_raw,
                    max_length=2000,
                    keep_last=True,
                    interaction=interaction,
                )
                previous_response = (
                    "Immediate message (already shown to the user):\n"
                    f"{canned_trunc or '(No canned text)'}\n\n"
                    "Further assistant content already sent:\n"
                    f"{prev_trunc or '(No previous response)'}"
                )
            else:
                previous_response = await Conversation.truncate_statement(
                    response_raw,
                    max_length=2000,
                    keep_last=True,
                    interaction=interaction,
                )
            continuation_guidance = CONTINUATION_GUIDANCE_PROMPT.format(
                previous_response=previous_response or "(No previous response)",
                user_utterance=user_utterance or "(No user utterance)",
            )
        elif canned_raw:
            canned_trunc = await Conversation.truncate_statement(
                canned_raw,
                max_length=500,
                interaction=interaction,
            )
            canned_lead_in_section = CANNED_LEAD_IN_CONTEXT_PROMPT.format(
                canned_text=canned_trunc or "(No canned text)"
            )

        all_capabilities = []
        if self.persona_capabilities:
            all_capabilities.extend(self.persona_capabilities)
        # Aggregate capabilities from enabled actions (e.g. WhatsApp, future Slack)
        agent = await self.get_agent()
        if agent:
            actions_manager = await agent.get_actions_manager()
            if actions_manager:
                enabled_actions = await actions_manager.get_actions(enabled_only=True)
                for action in enabled_actions:
                    all_capabilities.extend(action.get_capabilities())
        capabilities_str = "\n".join(f"- {cap}" for cap in all_capabilities)

        interpretation_section = (
            INTERPRETATION_INSIGHTS_PROMPT.format(
                interpretation=interaction.interpretation
            )
            if interaction.interpretation and interaction.interpretation.strip()
            else ""
        )

        # Vision instruction when images are attached (overrides history/training bias)
        vision_instruction_section = ""
        if visitor:
            data = getattr(visitor, "data", None) or {}
            media_urls = data.get("image_urls") or data.get("whatsapp_media") or []
            if media_urls:
                vision_instruction_section = VISION_IMAGE_INSTRUCTION
        vision_instruction_section = format_conditional_section(
            vision_instruction_section, bool(vision_instruction_section)
        )

        # Inject recent image context when current request has no images (follow-up questions)
        recent_image_context_section = ""
        if visitor:
            data = getattr(visitor, "data", None) or {}
            has_images = bool(data.get("image_urls"))
            suppressed = data.get("image_interpretation") is False
            if not has_images and not suppressed and interaction.conversation_id:
                conversation = await Conversation.get(interaction.conversation_id)
                if conversation:
                    interactions = await conversation.get_interactions(
                        limit=10, reverse=True
                    )
                    for prev in interactions:
                        if prev.id != interaction.id and prev.image_interpretation:
                            recent_image_context_section = (
                                f"RECENT IMAGE CONTEXT: The user may be referring to "
                                f"images from a previous message. Here is the detailed "
                                f"description: {prev.image_interpretation}"
                            )
                            break
        recent_image_context_section = format_conditional_section(
            recent_image_context_section, bool(recent_image_context_section)
        )

        # Build directives section
        if applicable_directives:
            directive_list = "\n".join(
                f"{i+1}. {d.get('content', str(d))}"
                for i, d in enumerate(applicable_directives)
            )
            directives_section = DIRECTIVES_SECTION_PROMPT.format(
                directive_list=directive_list,
                directive_count=len(applicable_directives),
            )
        else:
            directives_section = NO_DIRECTIVES_SUB_PROMPT

        # Fetch active tasks (used for parameter filtering and active_tasks_section)
        tasks: List[Dict[str, Any]] = []
        if self.remind_on_active_tasks:
            tasks = await self._get_active_tasks_requiring_intervention(interaction)

        # Filter parameters: exclude requires_active_tasks when no active tasks
        has_active_tasks = bool(tasks)
        filtered_parameters = [
            p
            for p in applicable_parameters
            if not p.get("requires_active_tasks") or has_active_tasks
        ]

        # Build parameters section
        if filtered_parameters:
            parameter_list = "\n".join(
                format_parameter(p, index=i + 1)
                for i, p in enumerate(filtered_parameters)
            )
            parameters_section = PARAMETERS_SUB_PROMPT.format(
                parameter_list=parameter_list
            )
        else:
            parameters_section = ""

        # Build active tasks section (when tasks require user intervention)
        active_tasks_section = ""
        if tasks:
            task_list = "\n".join(f"- {t.get('description', str(t))}" for t in tasks)
            active_tasks_section = ACTIVE_TASKS_SECTION_PROMPT.format(
                task_list=task_list
            )
        active_tasks_section = format_conditional_section(
            active_tasks_section, bool(active_tasks_section)
        )

        # Build response length section (channel-aware)
        use_voice = self._use_voice_formatting(interaction, visitor)
        if use_voice:
            word_limit = self.voice_response_limit
        else:
            word_limit = self._non_voice_response_word_limit(interaction)
        if word_limit > 0:
            response_length_section = RESPONSE_LENGTH_PROMPT.format(limit=word_limit)
        else:
            response_length_section = ""
        response_length_section = format_conditional_section(
            response_length_section, bool(response_length_section)
        )

        # Build channel formatting section (with override preamble when channel present)
        effective_channel = "voice" if use_voice else (interaction.channel or "default")
        channel_directive = get_channel_directive(
            effective_channel,
            phonetic_substitutions=(self.phonetic_substitutions if use_voice else None),
            voice_max_words=self.voice_response_limit,
        )
        if channel_directive:
            channel_formatting_section = f"### CHANNEL FORMATTING\n{CHANNEL_OVERRIDE_PREAMBLE}\n\n{channel_directive}"
        else:
            channel_formatting_section = ""

        # Format conditional sections
        interpretation_section = format_conditional_section(
            interpretation_section, bool(interpretation_section)
        )
        directives_section = format_conditional_section(
            directives_section, bool(directives_section)
        )
        parameters_section = format_conditional_section(
            parameters_section, bool(parameters_section)
        )
        channel_formatting_section = format_conditional_section(
            channel_formatting_section, bool(channel_formatting_section)
        )
        continuation_guidance = format_conditional_section(
            continuation_guidance, bool(continuation_guidance)
        )
        canned_lead_in_section = format_conditional_section(
            canned_lead_in_section, bool(canned_lead_in_section)
        )

        # Build examples section
        examples_section = ""
        if self.expression_examples:
            examples_list = "\n".join(
                f"User: {ex.get('user', '')}\nAssistant: {ex.get('assistant', '')}\n"
                for ex in self.expression_examples
            )
            examples_section = EXAMPLES_SECTION_PROMPT.format(
                examples_list=examples_list
            )
        examples_section = format_conditional_section(
            examples_section, bool(examples_section)
        )

        prompt_template = (
            self.system_prompt if self.system_prompt else SYSTEM_PROMPT_TEMPLATE
        )
        composed = prompt_template.format(
            agent_name=self.persona_name,
            agent_description=self.persona_description,
            agent_capabilities=capabilities_str,
            user=await self._get_user_display_name(interaction),
            date=date_str,
            time=time_str,
            timezone=timezone_str,
            examples_section=examples_section,
            vision_instruction_section=vision_instruction_section,
            interpretation_section=interpretation_section,
            recent_image_context_section=recent_image_context_section,
            canned_lead_in_section=canned_lead_in_section,
            response_protocol=RESPONSE_PROTOCOL_PROMPT,
            directives_section=directives_section,
            parameters_section=parameters_section,
            active_tasks_section=active_tasks_section,
            channel_formatting_section=channel_formatting_section,
            continuation_guidance=continuation_guidance,
            response_length_section=response_length_section,
        )

        # Append compliance check for directive recency reinforcement
        if applicable_directives:
            composed += "\n\n" + DIRECTIVE_COMPLIANCE_CHECK_PROMPT

        return composed

    async def _get_active_tasks_requiring_intervention(
        self, interaction: Interaction
    ) -> List[Dict[str, Any]]:
        """Get active tasks that require user intervention.

        Filters tasks by metadata.requires_user_intervention (default True).

        Args:
            interaction: Current interaction with conversation_id

        Returns:
            List of task dicts requiring user intervention
        """
        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []
        tasks = conversation.get_tasks(status="active")
        return [
            t
            for t in tasks
            if t.get("metadata", {}).get("requires_user_intervention", True)
        ]

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
        max_statement_length: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get formatted conversation history for the language model.

        Includes previous interactions and the current interaction when it has a
        canned lead-in and/or accumulated response, so the model sees the same order
        the user saw (transient canned message + optional follow-on content).

        Args:
            interaction: Current interaction
            history_limit: Number of past interactions to include
            with_utterance: Include user utterances in history (default: True)
            with_response: Include AI responses in history (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: False)
            max_statement_length: Truncate utterances/responses to this length (default: None)

        Returns:
            List of message dictionaries with 'role' and 'content' keys, or None if disabled
        """
        if history_limit <= 0:
            return None

        from jvagent.memory.conversation import Conversation

        # Get conversation
        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

        # Get conversation history from previous interactions (excluding current)
        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        # Helper function for truncation
        def _truncate(content: str) -> str:
            if max_statement_length and len(content) > max_statement_length:
                return content[:max_statement_length] + "..."
            return content

        # Current turn tail: user, then canned (if any, deduped), then main response
        canned_raw = (getattr(interaction, "canned_response", None) or "").strip()
        response_raw = (interaction.response or "").strip()
        canned_absorbed = bool(
            canned_raw and response_raw and response_raw.startswith(canned_raw)
        )
        has_canned_only = bool(
            with_response
            and canned_raw
            and not canned_absorbed
            and not interaction.response
        )
        has_response = bool(with_response and interaction.response)
        has_assistant_tail = has_canned_only or has_response

        if with_utterance and has_assistant_tail:
            history.append(
                {
                    "role": "user",
                    "content": _truncate(interaction.utterance or ""),
                }
            )
            if with_response:
                if canned_raw and not canned_absorbed:
                    history.append(
                        {
                            "role": "assistant",
                            "content": _truncate(canned_raw),
                        }
                    )
                if interaction.response:
                    history.append(
                        {
                            "role": "assistant",
                            "content": _truncate(interaction.response),
                        }
                    )
        elif with_response and has_assistant_tail:
            if canned_raw and not canned_absorbed:
                history.append(
                    {
                        "role": "assistant",
                        "content": _truncate(canned_raw),
                    }
                )
            if interaction.response:
                history.append(
                    {
                        "role": "assistant",
                        "content": _truncate(interaction.response),
                    }
                )

        return history if history else []

    async def _generate_response(
        self,
        interaction: Interaction,
        visitor: Optional[Any],
        applicable_directives: List[Dict[str, Any]],
        applicable_parameters: List[Dict[str, Any]],
        use_history: bool,
        history_limit: int,
        with_utterance: bool,
        with_interpretation: bool,
        with_event: bool,
        with_response: bool,
        max_statement_length: Optional[int],
        transient: bool,
        model_action: Optional[Any] = None,
    ) -> str:
        """Generate response using direct prompt approach.

        Composes the system prompt using _compose_prompt() and makes the language
        model call via model_action.generate() with system prompt and conversation history.

        Args:
            interaction: The active interaction object
            visitor: Optional InteractWalker for streaming support
            applicable_directives: List of unexecuted directives
            applicable_parameters: List of unexecuted parameters
            use_history: Whether to include conversation history
            history_limit: Number of past interactions to include
            with_utterance: Whether to include user utterances
            with_interpretation: Include interpretations in history
            with_event: Include events in history
            with_response: Include AI responses in history
            max_statement_length: Truncate to this length
            transient: If True, skip appending response to interaction.response
            model_action: Pre-fetched model action (avoids second lookup when passed from respond())

        Returns:
            Generated response string
        """
        if model_action is None:
            model_action = await self.get_model_action(required=True)

        # Generate and persist extensive image interpretation (behind the scenes) when
        # vision-capable images are present and interpretation is not suppressed.
        if visitor:
            data = getattr(visitor, "data", None) or {}
            image_urls = data.get("image_urls") or []
            if image_urls and data.get("image_interpretation") is not False:
                try:
                    vision_pass_model_action = (
                        await self._get_vision_model_action() or model_action
                    )
                    vision_pass_kwargs: dict = {}
                    if self.vision_model:
                        vision_pass_kwargs["model"] = self.vision_model
                    if self.vision_model_temperature is not None:
                        vision_pass_kwargs["temperature"] = (
                            self.vision_model_temperature
                        )
                    if self.vision_model_max_tokens is not None:
                        vision_pass_kwargs["max_tokens"] = self.vision_model_max_tokens
                    interpretation = await generate_image_interpretation(
                        image_urls, vision_pass_model_action, **vision_pass_kwargs
                    )
                    if interpretation:
                        interaction.image_interpretation = interpretation
                        await interaction.save()
                except Exception as e:
                    logger.warning(
                        f"PersonaAction: Failed to generate image interpretation: {e}"
                    )

        conversation_history = None
        if use_history:
            conversation_history = await self._get_conversation_history(
                interaction,
                history_limit,
                with_utterance=with_utterance,
                with_response=with_response,
                with_interpretation=with_interpretation,
                with_event=with_event,
                max_statement_length=max_statement_length,
            )

            # for reply coherence
            if with_interpretation and not with_response and interaction.response:
                conversation_history.append(
                    {
                        "role": "assistant",
                        "content": interaction.response,
                    }
                )

        streaming = bool(
            visitor
            and getattr(visitor, "stream", False)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Get ResponseBus from visitor
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        prompt = interaction.utterance if with_utterance else ""

        # Remind the model to follow directives in the system prompt (without restating them)
        if applicable_directives and prompt:
            prompt = f"{prompt}\n\n[SYSTEM: You MUST execute the directives provided in the system prompt in your response.]"

        # When voice formatting (channel=voice or respond_with_voice), inject format reminder
        use_voice = self._use_voice_formatting(interaction, visitor)
        if use_voice and prompt:
            prompt = f"{prompt}\n\n[VOICE: Plain text only. Max {self.voice_response_limit} words. No markdown, lists, or **bold**.]"

        # Build multimodal prompt if image URLs present in visitor.data
        prompt = build_prompt_for_vision(prompt, visitor, model_action)

        # Make the language model call
        try:
            # Enable JSON response format if structured output is requested
            response_format = (
                {"type": "json_object"} if self.use_structured_output else None
            )

            max_tokens = self.voice_max_tokens if use_voice else self.model_max_tokens
            response = await model_action.generate(
                prompt=prompt,
                stream=streaming,
                system=await self._compose_prompt(
                    interaction, applicable_directives, applicable_parameters, visitor
                ),
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=max_tokens,
                response_bus=response_bus if streaming else None,
                interaction=interaction,
                response_format=response_format,
                transient=transient,
            )

            # Parse structured output if enabled
            if self.use_structured_output and response:
                parsed_response = self._parse_structured_output(response)
                if parsed_response:
                    response = parsed_response
                # If parsing fails, response remains as-is (fallback)

            # Sanitize for voice channel (strip markdown, truncate) when model disobeys
            if use_voice and response:
                response = self._sanitize_voice_response(response)

            # Mark directives and parameters as executed only if we got a meaningful response
            if response and response.strip():
                if applicable_directives:
                    interaction.set_to_executed(directives=applicable_directives)
                if applicable_parameters:
                    interaction.set_to_executed(parameters=applicable_parameters)

            await self._pipe_response(
                response, interaction, visitor, streaming, transient
            )

            # Record PersonaAction execution AFTER response is generated and saved
            interaction.record_action_execution("PersonaAction")

            return response

        except Exception as e:
            logger.error(
                f"Error in PersonaAction._generate_response: {e}", exc_info=True
            )
            raise

    async def healthcheck(self) -> bool:
        """Check if the PersonaAction is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Check if model action is available
            action = await self.get_model_action()
            if not action:
                return False

            return True
        except Exception as e:
            logger.error(f"Healthcheck failed: {e}", exc_info=True)
            return False
