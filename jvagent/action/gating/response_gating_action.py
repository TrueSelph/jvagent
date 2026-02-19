"""ResponseGatingAction for realistic conversational behavior.

Classifies each utterance's response posture (RESPOND / SUPPRESS / DEFER)
to determine when the agent should reply, stay silent, or accumulate fragments.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.gating.gating_result import (
    GatingResult,
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    parse_gating_response,
)
from jvagent.action.gating.prompts import (
    GATING_PRIOR_FRAGMENTS_SECTION,
    GATING_PROMPT_TEMPLATE,
    GATING_SYSTEM_PROMPT,
    GATING_TASK_DEFAULT,
    GATING_TASK_WITH_FRAGMENTS,
)
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

BUFFER_KEY = "gating_deferred_fragments"


class ResponseGatingAction(InteractAction):
    """Gating action that classifies whether an utterance warrants a response.

    Runs before InteractRouter (weight=-200) to:
    1. Classify posture: RESPOND | SUPPRESS | DEFER
    2. On SUPPRESS: clear walk path, no response
    3. On DEFER: append to buffer, clear walk path, no response
    4. On RESPOND: consume buffer if non-empty, inject consolidated directive, proceed
    """

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of LanguageModelAction to use for LLM calls",
    )
    model: str = attribute(
        default="gpt-4o-mini",
        description="Model identifier (fast model recommended for gating)",
    )
    model_temperature: float = attribute(
        default=0.1,
        description="Temperature for LLM generation",
    )
    model_max_tokens: int = attribute(
        default=150,
        description="Max tokens for gating (lightweight call)",
    )
    history_limit: int = attribute(
        default=5,
        description="Number of previous interactions to include",
        ge=0,
    )
    enable_accumulation: bool = attribute(
        default=True,
        description="Enable DEFER posture and fragment accumulation",
    )
    max_fragment_buffer: int = attribute(
        default=5,
        description="Max deferred fragments to retain; drop oldest when exceeded",
        ge=1,
        le=20,
    )
    weight: int = attribute(
        default=-200,
        description="Execution weight (runs before InteractRouter)",
    )
    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute posture classification and apply gating logic."""
        interaction: Interaction | None = visitor.interaction
        if not interaction:
            logger.warning("ResponseGatingAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        conversation: Conversation | None = visitor.conversation
        if not conversation:
            logger.warning("ResponseGatingAction: No conversation available")
            await visitor.unrecord_action_execution()
            return

        try:
            model_action = await self.get_model_action()
            if not model_action:
                logger.warning("ResponseGatingAction: Model action not found, proceeding as RESPOND")
                return

            interaction_history = await conversation.get_interaction_history(
                limit=self.history_limit,
                excluded=interaction.id,
                with_utterance=True,
                with_response=True,
                with_interpretation=False,
                with_event=False,
                formatted=True,
            )
            history_text = self._format_history(interaction_history)

            buffer = list(conversation.context.get(BUFFER_KEY, []))
            prior_fragments = [b.get("utterance", "").strip() for b in buffer if b.get("utterance")]

            prompt = self._build_gating_prompt(
                history_text=history_text,
                utterance=interaction.utterance or "",
                prior_fragments=prior_fragments,
            )

            response = await model_action.generate(
                prompt=prompt,
                system=GATING_SYSTEM_PROMPT,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                model=self.model,
                calling_action_name=self.get_class_name(),
            )

            result = parse_gating_response(response)

            interaction.response_posture = result.posture
            await interaction.save()

            if result.is_suppress():
                await self._handle_suppress(visitor)
                return

            if result.is_defer() and self.enable_accumulation:
                await self._handle_defer(visitor, interaction, conversation)
                return

            if result.is_respond():
                await self._handle_respond(visitor, interaction, conversation)
                return

            logger.debug(f"ResponseGatingAction: Unknown posture {result.posture}, proceeding as RESPOND")
            await self._handle_respond(visitor, interaction, conversation)

        except Exception as e:
            logger.error(f"ResponseGatingAction: Error during execution: {e}", exc_info=True)
            await visitor.unrecord_action_execution()

    def _build_gating_prompt(
        self,
        history_text: str,
        utterance: str,
        prior_fragments: List[str],
    ) -> str:
        """Build the gating prompt, including prior fragments and combined-completeness task when applicable."""
        if prior_fragments:
            fragments_list = "\n".join(f'  {i + 1}. "{f}"' for i, f in enumerate(prior_fragments))
            prior_fragments_section = GATING_PRIOR_FRAGMENTS_SECTION.format(
                fragments_list=fragments_list,
            )
            task_instruction = GATING_TASK_WITH_FRAGMENTS
        else:
            prior_fragments_section = ""
            task_instruction = GATING_TASK_DEFAULT

        return GATING_PROMPT_TEMPLATE.format(
            history=history_text or "(No previous conversation)",
            prior_fragments_section=prior_fragments_section,
            utterance=utterance,
            task_instruction=task_instruction,
        )

    def _format_history(self, interaction_history: List[Dict[str, Any]]) -> str:
        """Format interaction history for the gating prompt."""
        if not interaction_history:
            return "(No previous conversation)"

        lines = []
        for entry in interaction_history:
            if isinstance(entry, dict):
                role = entry.get("role", "")
                content = (entry.get("content") or "").strip()
                if not content:
                    continue
                if role == "user":
                    lines.append(f"User: {content}")
                elif role == "assistant":
                    lines.append(f"Assistant: {content}")
        if lines:
            lines.append("")
            lines.append("---")
            lines.append(">>> USER RESPONDS NOW <<<")
            lines.append("---")
        return "\n".join(lines) if lines else "(No previous conversation)"

    async def _handle_suppress(self, visitor: "InteractWalker") -> None:
        """Clear walk path so no response is emitted."""
        await visitor.set_walk_path([])
        logger.info("ResponseGatingAction: SUPPRESS - cleared walk path, no response")

    async def _handle_defer(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
        """Append utterance to buffer and clear walk path."""
        buffer = list(conversation.context.get(BUFFER_KEY, []))
        buffer.append({
            "utterance": interaction.utterance or "",
            "interaction_id": interaction.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(buffer) > self.max_fragment_buffer:
            buffer = buffer[-self.max_fragment_buffer:]
        await conversation.update_context({BUFFER_KEY: buffer})
        await visitor.set_walk_path([])
        logger.info(
            f"ResponseGatingAction: DEFER - appended to buffer ({len(buffer)} fragments), no response"
        )

    async def _handle_respond(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
        """Consume buffer if non-empty, inject directive, let pipeline proceed."""
        buffer = list(conversation.context.get(BUFFER_KEY, []))
        if buffer:
            fragments = [b.get("utterance", "").strip() for b in buffer if b.get("utterance")]
            if fragments:
                directive = (
                    f"The user's current message completes a fragmented thought. "
                    f"Prior fragments: {repr(fragments)}. Treat them as a unified request."
                )
                await visitor.add_directive(directive)
                logger.info(
                    f"ResponseGatingAction: RESPOND - injected directive with {len(fragments)} prior fragments"
                )
            await conversation.update_context({BUFFER_KEY: []})
            await conversation.save()
