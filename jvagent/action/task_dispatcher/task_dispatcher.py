import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from jvagent.memory.conversation import Conversation
from jvagent.action.base import Action
from jvagent.action.interact.endpoints import interact_endpoint

logger = logging.getLogger(__name__)

class TaskDispatcher(Action):
    """Dispatches proactive tasks that have reached their trigger time."""

    async def tick(self, dry_run: bool = False, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """Check for triggered tasks and dispatch them.

        This uses the indexed fields to find only conversations with due tasks.
        If conversation_id is provided, it further narrows the search to that specific conversation.
        """
        from jvagent.core.app import App
        app = await App.get()
        now_dt = await app.now() if app else datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        # Format explicitly to YYYY-MM-DDTHH:MM to avoid seconds/timezone differences masking string DB matches
        now = now_dt.strftime("%Y-%m-%dT%H:%M")

        # 1. Build Query
        # We query for Conversations where any active_tasks item has status='active' AND next_trigger_at <= now
        # If conversation_id is set, we use session_id filter for targeted dispatch.
        query = {
            "active_tasks": {
                "$elemMatch": {
                    "status": "active",
                    "next_trigger_at": {"$lte": now}
                }
            }
        }
        if conversation_id:
            query["session_id"] = conversation_id

        from jvagent.core.cache import get_cached_agent
        try:
            agent = await get_cached_agent(self.agent_id)
            if not agent:
                logger.error(f"TaskDispatcher: Agent {self.agent_id} not found.")
                return {"error": "Agent not found"}

            memory = await agent.get_memory()
            if not memory:
                logger.error(f"TaskDispatcher: Memory not found for agent {self.agent_id}.")
                return {"error": "Memory not found"}

            from jvagent.memory.user import User

            # 1. Global query for all due tasks (highly efficient via specialized index)
            due_convs = await Conversation.find(query)
            if due_convs:
                logger.info(f"TaskDispatcher: Found {len(due_convs)} conversations with due tasks. (Targeted ID: {conversation_id or 'none'})")

            # 2. Filter for tasks belonging to THIS agent
            all_convs = []
            for conv in due_convs:
                # Get the user owning this conversation
                user = await conv.node(direction="in", node=User)
                if user and (user.memory_id == memory.id or await memory.is_connected_to(user)):
                    all_convs.append(conv)

            triggered_tasks = []
            dispatched_count = 0

            for conv in all_convs:
                due_tasks = []
                for t in conv.active_tasks:
                    if t.get("status") != "active":
                        continue

                    trigger_at = t.get("next_trigger_at")
                    if not trigger_at:
                        continue

                    # Standardize space to T for correct lexicographical string comparison
                    # (LLMs often output 'YYYY-MM-DD HH:MM' instead of 'YYYY-MM-DDTHH:MM')
                    trigger_at_str = str(trigger_at).replace(" ", "T")
                    if trigger_at_str <= now:
                        due_tasks.append(t)

                if not due_tasks:
                    continue

            # Ensure actions are initialized once before dispatching (required for Lambda/cold starts)
            # to register channel adapters on the ResponseBus.
            await app.initialize_actions()
            response_bus = await agent.get_response_bus()

            for conv in all_convs:
                due_tasks = []
                for t in conv.active_tasks:
                    if t.get("status") != "active":
                        continue

                    trigger_at = t.get("next_trigger_at")
                    if not trigger_at:
                        continue

                    # Standardize space to T for correct lexicographical string comparison
                    # (LLMs often output 'YYYY-MM-DD HH:MM' instead of 'YYYY-MM-DDTHH:MM')
                    trigger_at_str = str(trigger_at).replace(" ", "T")
                    if trigger_at_str <= now:
                        due_tasks.append(t)

                if not due_tasks:
                    continue

                # Dispatch tasks in parallel with a semaphore to bound concurrency
                semaphore = asyncio.Semaphore(5)

                async def dispatch_task(task_to_dispatch):
                    nonlocal dispatched_count
                    async with semaphore:
                        task_id = task_to_dispatch.get("task_id")
                        description = task_to_dispatch.get("description")

                        if dry_run:
                            logger.info(f"TaskDispatcher [Dry Run]: Would dispatch task '{description}' for conversation {conv.session_id}")
                            triggered_tasks.append({"session_id": conv.session_id, "task_id": task_id, "status": "dry_run"})
                            return

                        # 2. Dispatch Task
                        # Debug print in cyan
                        # Task fired
                        logger.info(f"TaskDispatcher: Dispatching task '{description}' for session {conv.session_id}")

                        # Atomically mark as 'triggered' so we don't double-fire if another tick comes
                        await conv.update_task(status="triggered", task_id=task_id)

                        # 3. Create a system utterance based on the task context
                        metadata = task_to_dispatch.get("metadata", {})
                        context = metadata.get("context", "Time to follow up.")
                        task_channel = metadata.get("channel")

                        system_utterance = f"[SYSTEM_PROMPT: TASK_TRIGGER] Task: {description}. Context: {context}"

                        try:
                            from jvagent.action.interact.interact_walker import InteractWalker

                            walker = InteractWalker(
                                agent_id=self.agent_id,
                                utterance=system_utterance,
                                channel=task_channel or conv.channel or "default",
                                session_id=conv.session_id,
                                user_id=conv.user_id,
                                response_bus=response_bus,  # CRITICAL: Fix for message delivery to jvchat/adapters
                                data={"is_proactive": True, "task_id": task_id}
                            )

                            await walker.spawn(agent)
                            interaction = walker.interaction

                            if interaction:
                                await interaction.close_interaction()
                                # We don't wait for flush because we are in a background loop potentially
                                # but we do want to ensure it's saved.
                                from jvspatial import flush_deferred_entities
                                await flush_deferred_entities(interaction, conv, strict=False)

                                dispatched_count += 1
                                triggered_tasks.append({
                                    "session_id": conv.session_id,
                                    "task_id": task_id,
                                    "status": "dispatched",
                                    "interaction_id": interaction.id
                                })
                            else:
                                logger.error(f"TaskDispatcher: Failed to create interaction for task {task_id}")

                        except Exception as e:
                            logger.error(f"TaskDispatcher: Failed to dispatch task {task_id}: {e}", exc_info=True)
                            pass

                # Fire all due tasks for this conversation concurrently
                await asyncio.gather(*(dispatch_task(task) for task in due_tasks))

            return {
                "triggered_count": len(triggered_tasks),
                "dispatched_count": dispatched_count,
                "tasks": triggered_tasks,
                "timestamp": now
            }

        except Exception as e:
            logger.error(f"TaskDispatcher: Tick error: {e}", exc_info=True)
            return {"error": str(e)}
