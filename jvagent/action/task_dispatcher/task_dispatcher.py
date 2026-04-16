import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from jvagent.memory.conversation import Conversation
from jvagent.action.base import Action
from jvagent.action.interact.endpoints import interact_endpoint

# try:
from jvspatial.api.integrations.scheduler.decorators import on_schedule
# except ImportError:
#     on_schedule = None

logger = logging.getLogger(__name__)
SCHEDULE_TASK_ID = "system_task_dispatcher"

# Module-level cache: survives across threads unlike ContextVars.
# Set once on successful scheduler startup; used to skip re-init in background threads.
_scheduler_service_ref = None

class TaskDispatcher(Action):
    """Dispatches proactive tasks that have reached their trigger time."""

    async def on_reload(self) -> None:
        await super().on_reload()
        await self._register_scheduler_task(warn_on_missing=True)

    async def on_deregister(self) -> None:
        await super().on_deregister()
        await self._unregister_scheduler_task()

    async def on_startup(self) -> None:
        await super().on_startup()
        # Server must be available by startup — warn if scheduler still can't start
        await self._register_scheduler_task(warn_on_missing=True)

    def _initialize_scheduler_service(self):
        global _scheduler_service_ref
        try:
            from jvspatial.api.context import get_current_server
            from jvspatial.api.integrations.scheduler.scheduler import (
                SchedulerConfig,
                SchedulerService,
            )

            server = get_current_server()
            if not server:
                # Expected when running inside the scheduler's background thread —
                # ContextVars don't propagate across threads.
                logger.debug("TaskDispatcher: no current server in context (background thread)")
                return _scheduler_service_ref  # Return cached ref if available

            existing_scheduler = getattr(server, "scheduler_service", None)
            if existing_scheduler:
                _scheduler_service_ref = existing_scheduler
                return existing_scheduler

            scheduler_interval = getattr(server.config, "scheduler_interval", 1)
            scheduler_config = SchedulerConfig(enabled=True, interval=scheduler_interval)
            scheduler_service = SchedulerService(
                config=scheduler_config,
                graph_context=getattr(server, "_graph_context", None),
            )
            server.scheduler_service = scheduler_service
            _scheduler_service_ref = scheduler_service

            try:
                if hasattr(server, "lifecycle_manager"):
                    def _stop_scheduler() -> None:
                        global _scheduler_service_ref
                        try:
                            if server.scheduler_service.is_running:
                                server.scheduler_service.stop()
                        except Exception as e:
                            logger.error(f"TaskDispatcher: failed to stop scheduler on shutdown: {e}")
                        finally:
                            _scheduler_service_ref = None

                    server.lifecycle_manager.add_shutdown_hook(_stop_scheduler)
            except Exception:
                pass

            return scheduler_service
        except Exception as e:
            logger.debug(f"TaskDispatcher: failed to initialize scheduler service: {e}")
            return None

    def _get_scheduler_service(self):
        global _scheduler_service_ref
        # Fast path: return cached module-level ref (works across threads)
        if _scheduler_service_ref is not None:
            return _scheduler_service_ref
        try:
            from jvspatial.api.context import get_current_server

            server = get_current_server()
            if server and hasattr(server, "scheduler_service") and getattr(server, "scheduler_service"):
                svc = getattr(server, "scheduler_service")
                _scheduler_service_ref = svc
                return svc

            return self._initialize_scheduler_service()
        except Exception as e:
            logger.debug(f"TaskDispatcher: failed to get current server scheduler_service: {e}")
        return None

    async def _register_scheduler_task(self, warn_on_missing: bool = False) -> None:
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            if warn_on_missing:
                logger.warning(
                    "TaskDispatcher: scheduler service unavailable after startup. "
                    "Ensure JVSPATIAL_SCHEDULER_ENABLED=true is set and the server config "
                    "has scheduler_enabled=True."
                )
            else:
                logger.debug(
                    "TaskDispatcher: scheduler service unavailable; "
                    "will retry from on_startup."
                )
            return

        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
        except Exception as e:
            logger.debug(f"TaskDispatcher: failed to unregister existing scheduler task before register: {e}")

        try:
            from jvspatial.api.integrations.scheduler.decorators import (
                get_scheduled_tasks,
                register_scheduled_tasks,
            )

            scheduled_tasks = get_scheduled_tasks()
            logger.debug(
                "TaskDispatcher: scheduled task registry contains %d tasks",
                len(scheduled_tasks),
            )

            await register_scheduled_tasks(scheduler_service)
            logger.info("TaskDispatcher: registered scheduler task '%s'", SCHEDULE_TASK_ID)

            if not scheduler_service.is_running:
                scheduler_service.start()
                logger.info("TaskDispatcher: started scheduler service")
        except Exception as e:
            logger.error(f"TaskDispatcher: failed to register scheduler task: {e}", exc_info=True)

    async def _unregister_scheduler_task(self) -> None:
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            return

        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
                logger.info("TaskDispatcher: unregistered scheduler task '%s'", SCHEDULE_TASK_ID)

                if not scheduler_service.list_tasks():
                    if scheduler_service.is_running:
                        scheduler_service.stop()
                        logger.info("TaskDispatcher: stopped scheduler service because no scheduled tasks remain")
        except Exception as e:
            logger.error(f"TaskDispatcher: failed to unregister scheduler task: {e}", exc_info=True)

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

                        # Task fired
                        logger.info(f"TaskDispatcher: Dispatching task '{description}' for session {conv.session_id}")

                        # Atomically mark as 'triggered' so we don't double-fire if another tick comes
                        # Using direct list manipulation as requested
                        tasks = getattr(conv, "active_tasks", [])
                        for t in tasks:
                            if t.get("task_id") == task_id:
                                t["status"] = "triggered"
                                t["updated_at"] = datetime.now(timezone.utc).isoformat()
                        await conv.save()

                        # 3. Create a system utterance based on the task context
                        metadata = task_to_dispatch.get("metadata", {})
                        context = metadata.get("context", "Time to follow up.")
                        task_channel = metadata.get("channel")
                        system_utterance = f"[SYSTEM_PROMPT: TASK_TRIGGER] Task: {description}. Context: {context}"

                        try:
                            from jvagent.action.interact.interact_walker import InteractWalker
                            from jvagent.action.persona.persona_action import PersonaAction

                            # 1. Ensure channel adapters are registered (Lazy init for background threads)
                            whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
                            if whatsapp_action:
                                await whatsapp_action.ensure_adapter_registered()

                            # 2. Get PersonaAction
                            persona = await agent.get_action_by_type("PersonaAction")
                            if not persona:
                                logger.error(f"TaskDispatcher: PersonaAction not found for agent {agent.id}; cannot dispatch task {task_id}")
                                return

                            # 3. Create walker for bus/metadata plumbing
                            walker = InteractWalker(
                                agent_id=self.agent_id,
                                utterance=system_utterance,
                                channel=task_channel or conv.channel or "default",
                                session_id=conv.session_id,
                                user_id=conv.user_id,
                                response_bus=response_bus,
                                data={"is_proactive": True, "task_id": task_id}
                            )

                            # 4. Resolve session and create interaction manually
                            # This ensures we have an interaction node to attach directives to.
                            memory = await agent.get_memory()
                            if not memory:
                                logger.error(f"TaskDispatcher: Memory node not found for agent {agent.id}")
                                return

                            # Resolve session (finds/creates user and conversation)
                            # Using *_ to be robust against signature changes in Memory.get_session
                            user, conversation, *_, is_new_user = await memory.get_session(
                                session_id=walker.session_id,
                                channel=walker.channel,
                                user_id=walker.user_id
                            )



                            # Create the persistent interaction node
                            interaction = await conversation.create_interaction(
                                utterance=system_utterance,
                                channel=walker.channel,
                                session_id=walker.session_id
                            )
                            
                            if interaction:
                                # Attach metadata via parameters as Interaction doesn't support a generic .data property
                                # We skip if walker.data is empty or invalid
                                if walker.data and isinstance(walker.data, dict):
                                    interaction.add_parameter(walker.data, "TaskDispatcher")
                                
                                await interaction.save()

                                walker.interaction = interaction
                                walker.conversation = conversation

                            if interaction:
                                # 5. Inject the Directive (forces LLM to focus on the task)
                                # We call interaction.add_directives directly to bypass the walker's 
                                # safety check requiring an active Action.execute context.
                                interaction.add_directives([system_utterance], "TaskDispatcher")
                                await interaction.save()

                                # 6. Generate response via PersonaAction
                                # This publishes to the bus and updates interaction.response
                                await persona.respond(interaction, visitor=walker)

                                # 7. Finalize (emits final signal to bus, saves history)
                                await walker._finalize()

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

# Global Native Scheduler Loop
if on_schedule:
    @on_schedule("every 2 minutes", task_id="system_task_dispatcher")
    async def _native_task_dispatcher_tick():
        """Background poller that invokes the task dispatcher loop natively without webhooks."""
        logger.debug("TaskDispatcher (Native): Running scheduled tick.")
        try:
            # Find all active dispatchers across the system
            dispatchers = await TaskDispatcher.find({
                "context.enabled": True,
            })
            if not dispatchers:
                return

            tick_tasks = []
            for dispatcher in dispatchers:
                # Fire them off concurrently for performance
                logger.debug(f"TaskDispatcher (Native): Ticking for agent {dispatcher.agent_id}")
                tick_tasks.append(dispatcher.tick(dry_run=False))

            results = await asyncio.gather(*tick_tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"TaskDispatcher (Native) failed: {res}")
        except Exception as e:
            logger.error(f"TaskDispatcher (Native) encountered a global error: {e}", exc_info=True)
