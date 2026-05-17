import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.core.app import App, app_now_aware_utc

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class TaskTriggerInteractAction(InteractAction):
    """Proactive task trigger.

    Runs at the start of the interaction (weight -180) to check if any
    pending proactive tasks match the current user utterance or context (mood).
    This action does NOT use an LLM and is designed for maximum speed.
    """

    weight: int = attribute(
        default=-180,
        description="Runs after InteractRouter (-200) to inject directives for the response.",
    )
    always_execute: bool = attribute(
        default=True,
        description="Always execute to ensure tasks are triggered as soon as conditions are met.",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Check pending PROACTIVE tasks and fire any whose trigger conditions are met."""
        interaction = visitor.interaction
        if not interaction:
            return

        # Prefer visitor.conversation for consistency
        conversation = (
            getattr(visitor, "conversation", None)
            or await interaction.get_conversation()
        )
        if not conversation:
            return

        await self._check_task_triggers(visitor, interaction, conversation)

    async def _check_task_triggers(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
        """Ported from InteractRouter/BrainAction to handle triggering in a dedicated step.

        Evaluates keyword matches in utterance and mood matches in inner_monologue.
        """
        try:
            app = await App.get()
            now = await app_now_aware_utc(app)
        except Exception:
            now = datetime.now(timezone.utc)

        # Standardize 'now' into a comparable string format (YYYY-MM-DDTHH:MM)
        now_str = now.strftime("%Y-%m-%dT%H:%M")

        # Check for both 'active' (time/keyword triggers) and 'triggered' (already background-dispatched)
        pending_tasks = conversation.get_tasks(status=["active", "triggered"])
        triggered_count = 0

        for task in pending_tasks:
            if task.get("task_type") != "PROACTIVE":
                continue

            metadata = task.get("metadata", {})
            trigger_time_str = metadata.get("trigger_time")
            trigger_condition = metadata.get("trigger_condition", "none").lower()
            should_trigger = False

            # A) Time-based trigger (The HARD GATE)
            # AUDIT-actions XC-18: parse both ends into timezone-aware
            # ``datetime`` and compare as instants. Lexicographic string
            # comparison breaks the moment trigger_time_str carries a
            # timezone offset (e.g. ``2026-05-17T08:00-05:00``).
            if trigger_time_str:
                trigger_at_str = str(trigger_time_str).replace(" ", "T")
                try:
                    parsed = datetime.fromisoformat(
                        trigger_at_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    logger.warning(
                        "TaskTrigger: malformed trigger_time %r on task %s; "
                        "skipping",
                        trigger_time_str,
                        task.get("task_id"),
                    )
                    continue
                if parsed.tzinfo is None:
                    # Treat naïve trigger_time as UTC for backward-compat.
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed > now:
                    logger.debug(
                        f"TaskTrigger: Task '{task.get('description')}' is for "
                        f"the future ({parsed.isoformat()}). Skipping."
                    )
                    continue

                logger.info(
                    f"TaskTrigger: Time trigger '{trigger_time_str}' matched "
                    f"(due at {parsed.isoformat()}, now is {now.isoformat()})"
                )
                should_trigger = True

            # B) Keyword condition trigger (Only if no time or time passed)
            if not should_trigger and trigger_condition not in ("none", ""):
                utterance_lower = (interaction.utterance or "").lower()
                if trigger_condition in utterance_lower:
                    should_trigger = True
                    logger.info(
                        f"TaskTrigger: Keyword trigger '{trigger_condition}' matched in utterance"
                    )

            # C) Mood-based trigger (from inner monologue set by Router)
            if not should_trigger and trigger_condition not in ("none", ""):
                monologue = (interaction.inner_monologue or "").lower()
                mood_match = re.search(r"mood[:\s]+(\w+)", monologue, re.IGNORECASE)
                if mood_match:
                    detected_mood = mood_match.group(1).lower()
                    if trigger_condition == detected_mood:
                        should_trigger = True
                        logger.debug(
                            f"BrainTriggerAction: Mood trigger '{trigger_condition}' matched mood '{detected_mood}'"
                        )

            if should_trigger:
                directive = (
                    f"TASK FOLLOW-UP: {task.get('description', '')}\n"
                    f"CONTEXT: {metadata.get('context', '')}"
                )
                await visitor.add_directive(directive)

                # Mark as completed
                task_id = task.get("id")
                handle = visitor.tasks.get(task_id)
                if handle:
                    await handle.complete()
                    logger.info(f"TaskTrigger: Marked task {task_id} as completed.")
                else:
                    logger.warning(
                        f"TaskTrigger: Failed to find task {task_id} to mark as completed!"
                    )

                triggered_count += 1
                logger.info(
                    f"TaskTrigger: Triggered proactive task: {task.get('description')}"
                )

        if triggered_count > 0:
            await interaction.save()
            logger.info(
                f"TaskTrigger: Fired {triggered_count} proactive task(s) and saved interaction."
            )
