import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from jvagent.action.base import Action
from jvagent.action.interact.endpoints import interact_endpoint
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore

try:
    from jvspatial.api.integrations.scheduler.decorators import on_schedule
except ImportError:
    on_schedule = None

logger = logging.getLogger(__name__)
SCHEDULE_TASK_ID = "system_task_dispatcher"

# Per-Server scheduler-service cache. Keyed by ``id(server)`` so multiple
# jvagent apps embedded in the same Python process keep isolated
# references. AUDIT-actions Wave D.
_scheduler_service_refs: Dict[int, Any] = {}


def _server_key(server: Any) -> int:
    return id(server) if server is not None else 0


def _get_cached_scheduler(server: Any) -> Any:
    return _scheduler_service_refs.get(_server_key(server))


def _set_cached_scheduler(server: Any, value: Any) -> None:
    key = _server_key(server)
    if value is None:
        _scheduler_service_refs.pop(key, None)
    else:
        _scheduler_service_refs[key] = value


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
        try:
            from jvspatial.api.context import get_current_server
            from jvspatial.api.integrations.scheduler.scheduler import (
                SchedulerConfig,
                SchedulerService,
            )

            server = get_current_server()
            if not server:
                logger.debug(
                    "TaskDispatcher: no current server in context (background thread)"
                )
                return _get_cached_scheduler(server)

            existing_scheduler = getattr(server, "scheduler_service", None)
            if existing_scheduler:
                _set_cached_scheduler(server, existing_scheduler)
                return existing_scheduler

            scheduler_interval = getattr(server.config, "scheduler_interval", 1)
            scheduler_config = SchedulerConfig(
                enabled=True, interval=scheduler_interval
            )
            scheduler_service = SchedulerService(
                config=scheduler_config,
                graph_context=getattr(server, "_graph_context", None),
            )
            server.scheduler_service = scheduler_service
            _set_cached_scheduler(server, scheduler_service)

            try:
                if hasattr(server, "lifecycle_manager"):

                    def _stop_scheduler() -> None:
                        try:
                            if server.scheduler_service.is_running:
                                server.scheduler_service.stop()
                        except Exception as e:
                            logger.error(
                                f"TaskDispatcher: failed to stop scheduler on shutdown: {e}"
                            )
                        finally:
                            _set_cached_scheduler(server, None)

                    server.lifecycle_manager.add_shutdown_hook(_stop_scheduler)
            except Exception:
                pass

            return scheduler_service
        except Exception as e:
            logger.debug(f"TaskDispatcher: failed to initialize scheduler service: {e}")
            return None

    def _get_scheduler_service(self):
        try:
            from jvspatial.api.context import get_current_server

            server = get_current_server()
            cached = _get_cached_scheduler(server)
            if cached is not None:
                return cached
            if (
                server
                and hasattr(server, "scheduler_service")
                and getattr(server, "scheduler_service")
            ):
                svc = getattr(server, "scheduler_service")
                _set_cached_scheduler(server, svc)
                return svc
            return self._initialize_scheduler_service()
        except Exception as e:
            logger.debug(
                f"TaskDispatcher: failed to get current server scheduler_service: {e}"
            )
        return None

    async def _register_scheduler_task(self, warn_on_missing: bool = False) -> None:
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            if warn_on_missing:
                logger.warning(
                    "TaskDispatcher: scheduler service unavailable after startup."
                )
            return

        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
        except Exception:
            pass

        try:
            from jvspatial.api.integrations.scheduler.decorators import (
                get_scheduled_tasks,
                register_scheduled_tasks,
            )

            await register_scheduled_tasks(scheduler_service)
            if not scheduler_service.is_running:
                scheduler_service.start()
        except Exception as e:
            logger.error(
                f"TaskDispatcher: failed to register scheduler task: {e}", exc_info=True
            )

    async def _unregister_scheduler_task(self) -> None:
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            return
        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
        except Exception as e:
            logger.error(f"TaskDispatcher: failed to unregister scheduler task: {e}")

    async def tick(
        self, dry_run: bool = False, conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check for triggered tasks and dispatch them."""
        from jvagent.core.app import App, app_now_aware_utc

        app = await App.get()
        now_dt = await app_now_aware_utc(app)
        now = now_dt.strftime("%Y-%m-%dT%H:%M")

        query: Dict[str, Any] = {
            "tasks": {
                "$elemMatch": {"status": "active", "data.trigger_at": {"$lte": now}}
            }
        }
        if conversation_id:
            query["session_id"] = conversation_id

        from jvagent.core.cache import get_cached_agent

        try:
            agent = await get_cached_agent(self.agent_id)
            if not agent:
                return {"error": "Agent not found"}
            memory = await agent.get_memory()
            if not memory:
                return {"error": "Memory not found"}

            from jvagent.memory.user import User

            due_convs = await Conversation.find(query)

            all_convs = []
            for conv in due_convs:
                user = await conv.node(direction="in", node=User)
                if user and (
                    user.memory_id == memory.id or await memory.is_connected_to(user)
                ):
                    all_convs.append(conv)

            dispatched_count = 0
            semaphore = asyncio.Semaphore(5)

            async def dispatch_task(task_context, conv_id):
                nonlocal dispatched_count
                async with semaphore:
                    task_id = task_context.get("task_id")
                    description = task_context.get("description")
                    metadata = task_context.get("metadata", {})
                    context = metadata.get("context", "Time to follow up.")
                    task_channel = metadata.get("channel")
                    system_utterance = f"[SYSTEM_PROMPT: TASK_TRIGGER] Task: {description}. Context: {context}"

                    try:
                        from jvagent.memory.distributed_conversation_lock import (
                            conversation_mutation_lock,
                        )
                    except ImportError:

                        @asynccontextmanager
                        async def conversation_mutation_lock(id):
                            yield

                    logger.debug(
                        f"TaskDispatcher: Requesting lock for conversation {conv_id}..."
                    )
                    try:
                        async with conversation_mutation_lock(conv_id):
                            logger.debug(
                                f"TaskDispatcher: Lock ACQUIRED for {conv_id}. Reloading state..."
                            )

                            # 1. Reload the real conversation node
                            conversation = await Conversation.get(conv_id)
                            if not conversation:
                                logger.warning(
                                    f"TaskDispatcher: Conversation {conv_id} not found after reload."
                                )
                                return

                            # 2. Atomic status check + reservation
                            store = TaskStore(conversation)
                            t_handle = store.get(task_id)
                            if not t_handle:
                                logger.warning(
                                    f"TaskDispatcher: Task {task_id} not found in conversation {conv_id} tasks."
                                )
                                return

                            current_status = t_handle.status
                            logger.info(
                                f"TaskDispatcher: Processing task {task_id} (Status: {current_status})"
                            )

                            if current_status != "active":
                                logger.info(
                                    f"TaskDispatcher: SKIPPING task {task_id} because its status is '{current_status}' (not 'active')."
                                )
                                return

                            # Reserve by marking as failed with a note; the dispatch will create a new task
                            # In the new design we don't have 'reserved' status, so just proceed
                            logger.debug(
                                "TaskDispatcher: Task %s is active and will be dispatched.",
                                task_id,
                            )

                            if dry_run:
                                logger.info(f"Dry Run: Would dispatch {description}")
                                await t_handle.complete(
                                    result="Dry-run proactive dispatch."
                                )
                                return

                            # 3. Setup Walker & Persona
                            from jvagent.action.interact.interact_walker import (
                                InteractWalker,
                            )
                            from jvagent.action.persona.persona_action import (
                                PersonaAction,
                            )

                            persona = await agent.get_action_by_type("PersonaAction")
                            response_bus = await agent.get_response_bus()

                            walker = InteractWalker(
                                agent_id=agent.id,
                                utterance=system_utterance,
                                channel=task_channel
                                or conversation.channel
                                or "default",
                                session_id=conversation.session_id,
                                user_id=conversation.user_id,
                                response_bus=response_bus,
                                data={"is_proactive": True, "task_id": task_id},
                            )
                            walker.conversation = conversation

                            interaction = await conversation.create_interaction(
                                utterance=system_utterance,
                                channel=walker.channel,
                                session_id=walker.session_id,
                            )

                            if interaction:
                                interaction.add_parameter(walker.data, "TaskDispatcher")
                                interaction.add_directives(
                                    [system_utterance], "TaskDispatcher"
                                )
                                await interaction.save()
                                walker.interaction = interaction
                                await persona.respond(interaction, visitor=walker)
                                await walker._finalize()
                                await t_handle.complete(
                                    result="Proactive dispatch completed."
                                )
                                dispatched_count += 1
                    except Exception as e:
                        try:
                            conversation = await Conversation.get(conv_id)
                            if conversation:
                                store2 = TaskStore(conversation)
                                t2 = store2.get(task_id)
                                if t2:
                                    await t2.fail(reason=f"Dispatcher error: {e}")
                        except Exception:
                            pass
                        logger.error(f"Dispatch Error {task_id}: {e}", exc_info=True)

            dispatch_jobs = []
            for conv in all_convs:
                raw_tasks = getattr(conv, "tasks", None) or getattr(
                    conv, "active_tasks", []
                )
                for t in raw_tasks:
                    if t.get("status") == "active":
                        trig = str(
                            t.get("data", {}).get("trigger_at", "")
                            or t.get("next_trigger_at", "")
                        ).replace(" ", "T")
                        if trig <= now:
                            dispatch_jobs.append(dispatch_task(t, conv.id))

            if dispatch_jobs:
                await app.initialize_actions()
                await asyncio.gather(*dispatch_jobs)

            return {"dispatched": dispatched_count, "timestamp": now}
        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)
            return {"error": str(e)}


if on_schedule:

    @on_schedule("every 2 minutes", task_id="system_task_dispatcher")
    async def _native_task_dispatcher_tick():
        logger.debug("Ticking TaskDispatcher...")
        try:
            from jvagent.action.task_dispatcher.task_dispatcher import TaskDispatcher

            dispatchers = await TaskDispatcher.find({"context.enabled": True})
            if dispatchers:
                await asyncio.gather(
                    *(d.tick() for d in dispatchers), return_exceptions=True
                )
        except Exception as e:
            logger.error(f"Global Tick error: {e}")
