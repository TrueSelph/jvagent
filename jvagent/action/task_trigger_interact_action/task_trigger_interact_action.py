import logging
import uuid
from typing import TYPE_CHECKING, Any

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.task_monitor.task_monitor import attach_proactive_to_visitor
from jvagent.core.app import App, app_now_aware_utc
from jvagent.memory.task_eligibility import pick_next_proactive_task

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class TaskTriggerInteractAction(InteractAction):
    """Proactive task trigger.

    Runs before Orchestrator (weight -250) to claim and inject event-eligible
    PROACTIVE tasks on user turns. Completion is deferred to the Orchestrator
    finalizer — does not use an LLM.
    """

    weight: int = attribute(
        default=-250,
        description=(
            "Runs before Orchestrator (-200) so proactive directives are "
            "available to the orchestrator on the same turn."
        ),
    )
    always_execute: bool = attribute(
        default=True,
        description="Always execute to ensure tasks are triggered as soon as conditions are met.",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Claim and attach the next event-eligible PROACTIVE task, if any."""
        interaction = visitor.interaction
        if not interaction:
            return

        conversation = (
            getattr(visitor, "conversation", None)
            or await interaction.get_conversation()
        )
        if not conversation:
            return

        store = getattr(visitor, "tasks", None)
        if store is None:
            return

        try:
            app = await App.get()
            now = await app_now_aware_utc(app)
        except Exception:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)

        handle = pick_next_proactive_task(
            store,
            interaction=interaction,
            now=now,
        )
        if handle is None:
            return

        lease_id = str(uuid.uuid4())
        if not await store.claim_proactive(handle.id, lease_id):
            logger.debug(
                "TaskTrigger: failed to claim proactive task %s",
                handle.id,
            )
            return

        await attach_proactive_to_visitor(visitor, handle)
        await interaction.save()
        logger.info(
            "TaskTrigger: attached event-eligible proactive task %s",
            handle.id,
        )
