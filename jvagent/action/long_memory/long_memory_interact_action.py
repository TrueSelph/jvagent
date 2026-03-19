"""UserLongMemoryInteractAction

A passive background action that analyses conversation history and maintains
structured long-term memory as a graph of UserLongMemoryNode objects attached to
the User node — one node per memory category.

Default categories:
  - interests
  - facts_and_preferences
  - open_threads
  - recent_events

Custom categories can emerge dynamically from the LLM output.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.app import App
from jvagent.memory import Interaction

from .prompts import LONG_MEMORY_CUSTOM_UPDATE_PROMPT, LONG_MEMORY_UPDATE_PROMPT

logger = logging.getLogger(__name__)


class UserLongMemoryInteractAction(InteractAction):
    """Passive background action that maintains graph-based long-term memory.

    After each interaction (or every N interactions), this action:
      1. Reads the user's current UserLongMemory graph (per-category nodes).
      2. Sends the conversation history + current memory to an LLM.
      3. Receives a JSON map of category → updated markdown content.
      4. Writes each updated category back to its UserLongMemoryNode in the graph.
      5. Creates new category nodes for any novel categories the LLM introduces.

    Graph structure:
        User ──> UserLongMemory ──> UserLongMemoryNode (interests)
                             ──> UserLongMemoryNode (facts_and_preferences)
                             ──> UserLongMemoryNode (open_threads)
                             ──> UserLongMemoryNode (recent_events)
                             ──> UserLongMemoryNode (<custom added by LLM>)
    """

    # ── LLM configuration ────────────────────────────────────────────────────
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use",
    )
    model: str = attribute(
        default="gpt-4o",
        description="Model to use for memory extraction",
    )
    model_temperature: float = attribute(
        default=0.1, description="Low temperature for consistent extraction"
    )
    model_max_tokens: int = attribute(
        default=1024, description="Max tokens for memory updates"
    )

    # ── Behaviour configuration ───────────────────────────────────────────────
    update_frequency: int = attribute(
        default=3,
        description="Run extraction every N user messages (1 = every interaction)",
    )
    history_limit: int = attribute(
        default=6, description="Number of recent interactions to include in analysis"
    )
    points_of_interest: List[str] = attribute(
        default_factory=list,
        description="Optional list of topics to focus extraction on",
    )

    # ── Prompt overrides ──────────────────────────────────────────────────────
    update_prompt: str = attribute(
        default=LONG_MEMORY_UPDATE_PROMPT,
        description="System prompt for memory extraction (no custom POI)",
    )
    update_prompt_custom: str = attribute(
        default=LONG_MEMORY_CUSTOM_UPDATE_PROMPT,
        description="System prompt when points_of_interest are configured",
    )

    # ── Scheduling / execution ────────────────────────────────────────────────
    weight: int = attribute(default=150, description="Execution weight (high = late)")
    always_execute: bool = attribute(
        default=True, description="Always execute regardless of routing results"
    )
    run_in_background: bool = attribute(
        default=True,
        description="Run in the background after the response is sent",
    )
    collection: str = attribute(
        default="LongTermMemory",
        description="Collection to use for semantic memory extraction",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    async def execute(self, visitor: InteractWalker) -> None:
        """Execute the long memory update logic."""
        interaction = visitor.interaction
        if not interaction:
            return

        conv_history = await self._get_conversation_history(
            interaction, self.history_limit
        )

        num_user_msgs = self._count_user_messages_since_last_update(conv_history)
        logger.debug(f"UserLongMemoryAction: {num_user_msgs} user messages since last update")

        if num_user_msgs >= self.update_frequency:
            await self._update_user_long_memory(visitor)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _count_user_messages_since_last_update(
        self,
        conversation_history: List[Dict[str, Any]],
        event_marker: str = "[EVENT] UserLongMemoryInteractAction: Updated long memory",
    ) -> int:
        """Count user messages after the last long-memory update event.

        Returns the total number of user messages if no event marker exists,
        so the action always fires on first run.
        """
        last_event_idx = -1
        for i, msg in enumerate(conversation_history):
            if msg.get("role") == "system" and event_marker in msg.get("content", ""):
                last_event_idx = i

        if last_event_idx == -1:
            return sum(1 for m in conversation_history if m.get("role") == "user")

        return sum(
            1
            for m in conversation_history[last_event_idx + 1 :]
            if m.get("role") == "user"
        )

    def _poi_to_category_key(self, poi: str) -> str:
        """Convert a point of interest string to a snake_case category key."""
        clean = re.sub(r"[^\w\s-]", "", poi)
        return re.sub(r"[\s-]+", "_", clean).strip().lower()

    async def _update_user_long_memory(self, visitor: InteractWalker) -> None:
        """Run the LLM extraction and write results into the graph."""
        from jvagent.memory.user_long_memory import UserLongMemory

        interaction = visitor.interaction
        user = await interaction.get_user()
        if not user:
            logger.debug("UserLongMemoryAction: No user found, skipping")
            return

        user_id = visitor.user_id

        # ── Get or create the UserLongMemory graph for this user ─────────────────
        user_long_memory = await UserLongMemory.get_or_create_for_user(user)
        await user_long_memory.ensure_default_categories()

        # Ensure points of interest are also initialized as category nodes
        if self.points_of_interest:
            for poi in self.points_of_interest:
                cat_key = self._poi_to_category_key(poi)
                if cat_key:
                    await user_long_memory.get_or_create_category(cat_key, title=poi)

        # ── Read current state of all category nodes ──────────────────────────
        current_content_map = await user_long_memory.get_content_map()
        # Include default categories even if empty so the LLM knows they exist
        from jvagent.memory.user_long_memory import DEFAULT_CATEGORIES

        for cat in DEFAULT_CATEGORIES:
            current_content_map.setdefault(cat, "")

        # Also include POI categories so the LLM sees them in current_memory_json
        if self.points_of_interest:
            for poi in self.points_of_interest:
                cat_key = self._poi_to_category_key(poi)
                if cat_key:
                    current_content_map.setdefault(cat_key, "")

        # ── Build conversation history for the prompt ─────────────────────────
        history = await self._get_conversation_history(interaction, self.history_limit)
        if not history:
            return

        # ── Get LLM model action ──────────────────────────────────────────────
        model_action = await self.get_action(self.model_action_type)
        if not model_action:
            logger.warning(
                f"UserLongMemoryAction: Model action '{self.model_action_type}' not found"
            )
            return

        # ── Format date ───────────────────────────────────────────────────────
        app = await App.get()
        today_str = await app.now("%A, %B %d, %Y") if app else "unknown date"

        # ── Build system prompt ───────────────────────────────────────────────
        current_memory_json = json.dumps(current_content_map, indent=2)
        if self.points_of_interest:
            prompt = self.update_prompt_custom.format(
                points_of_interest=self.points_of_interest,
                current_memory_json=current_memory_json,
                today=today_str,
            )
        else:
            prompt = self.update_prompt.format(
                current_memory_json=current_memory_json,
                today=today_str,
            )

        # ── Call LLM ─────────────────────────────────────────────────────────
        try:
            raw_response = await model_action.generate(
                prompt=prompt,
                model=self.model,
                history=history,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"UserLongMemoryAction: LLM call failed: {e}", exc_info=True)
            return

        if not raw_response:
            logger.warning("UserLongMemoryAction: Empty response from LLM")
            return

        # ── Parse JSON response ───────────────────────────────────────────────
        try:
            if isinstance(raw_response, str):
                # Clean up potential markdown code blocks
                response_clean = raw_response.strip()
                if response_clean.startswith("```json"):
                    response_clean = response_clean[7:]
                if response_clean.startswith("```"):
                    response_clean = response_clean[3:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()

                updates = json.loads(response_clean)
            else:
                updates = raw_response
        except json.JSONDecodeError as e:
            logger.error(f"UserLongMemoryAction: Failed to parse JSON response: {e}")
            return

        # No-update sentinel
        if updates.get("_no_update"):
            logger.debug("UserLongMemoryAction: LLM returned no updates")
            interaction.add_event(
                "UserLongMemoryInteractAction: Updated long memory - No updates",
                self.get_class_name(),
            )
            await interaction.save()
            return

        # ── Write each category into its graph node ───────────────────────────
        any_changed = False
        for category, new_content in updates.items():
            if category.startswith("_"):
                continue  # skip internal sentinels
            if not isinstance(new_content, str) or not new_content.strip():
                continue

            category_node = await user_long_memory.get_or_create_category(category)
            changed = await category_node.update_content(new_content)
            if changed:
                logger.info(
                    f"UserLongMemoryAction: Updated category '{category}' for user {user_id}"
                )
                any_changed = True

        if any_changed:
            interaction.add_event(
                "UserLongMemoryInteractAction: Updated long memory",
                self.get_class_name(),
            )
        else:
            interaction.add_event(
                "UserLongMemoryInteractAction: Updated long memory - No meaningful changes",
                self.get_class_name(),
            )

        await interaction.save()

    # ─────────────────────────────────────────────────────────────────────────
    # Conversation history helper
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get formatted conversation history for the language model."""
        if history_limit <= 0:
            return []

        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

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

        def _truncate(content: str) -> str:
            if max_statement_length and len(content) > max_statement_length:
                return content[:max_statement_length] + "..."
            return content

        # Include current interaction utterance + response for multi-call awareness
        if with_utterance and with_response and interaction.response:
            history.append({"role": "user", "content": _truncate(interaction.utterance)})
            history.append({"role": "assistant", "content": _truncate(interaction.response)})
        elif with_response and interaction.response:
            history.append({"role": "assistant", "content": _truncate(interaction.response)})

        return history if history else []
