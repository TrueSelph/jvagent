"""Periodic monitor for conversation-scoped PROACTIVE task queues."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.task_monitor.finalize import (
    cancel_expired_pending,
    finalize_proactive_task,
    sweep_terminal_proactive,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_eligibility import pick_next_proactive_task
from jvagent.memory.task_proactive import PROACTIVE_TASK_TYPE, ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore

try:
    from jvspatial.api.integrations.scheduler.decorators import on_schedule
except ImportError:
    on_schedule = None

logger = logging.getLogger(__name__)
SCHEDULE_TASK_ID = "system_task_monitor"

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


class TaskMonitor(Action):
    """Dispatches eligible PROACTIVE tasks through the full Orchestrator."""

    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/proactive/tick/{agent_id}",
        "/proactive/webhooks/{agent_id}",
    ]

    enabled: bool = attribute(
        default=True, description="Master switch for proactive monitoring."
    )
    tick_interval: str = attribute(
        default="every 2 minutes",
        description="Scheduler expression for periodic ticks.",
    )
    max_parallel_conversations: int = attribute(
        default=5,
        description="Maximum conversations dispatched concurrently per tick.",
    )
    default_max_attempts: int = attribute(
        default=3,
        description="Default retry ceiling for proactive tasks without an explicit max.",
    )
    terminal_ttl_days: int = attribute(
        default=30,
        description=(
            "Remove terminal PROACTIVE tasks older than this many days on each "
            "tick. Set to 0 to disable the sweep (unbounded growth)."
        ),
    )

    async def on_reload(self) -> None:
        await super().on_reload()
        await self._register_scheduler_task(warn_on_missing=True)

    async def on_deregister(self) -> None:
        await super().on_deregister()
        await self._unregister_scheduler_task()

    async def on_startup(self) -> None:
        await super().on_startup()
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
                                "TaskMonitor: failed to stop scheduler on shutdown: %s",
                                e,
                            )
                        finally:
                            _set_cached_scheduler(server, None)

                    server.lifecycle_manager.add_shutdown_hook(_stop_scheduler)
            except Exception:
                pass

            return scheduler_service
        except Exception as e:
            logger.debug("TaskMonitor: failed to initialize scheduler service: %s", e)
            return None

    def _get_scheduler_service(self):
        try:
            from jvagent.core.scheduler_bootstrap import resolve_scheduler_service

            svc = resolve_scheduler_service()
            if svc is not None:
                from jvspatial.api.context import get_current_server

                server = get_current_server()
                _set_cached_scheduler(server, svc)
                return svc

            from jvspatial.api.context import get_current_server

            server = get_current_server()
            cached = _get_cached_scheduler(server)
            if cached is not None:
                return cached
            return self._initialize_scheduler_service()
        except Exception as e:
            logger.debug("TaskMonitor: failed to get scheduler service: %s", e)
        return None

    async def _register_scheduler_task(self, warn_on_missing: bool = False) -> None:
        if not self.enabled:
            return
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            if warn_on_missing:
                from jvspatial.runtime.serverless import is_serverless_mode

                from jvagent.core.scheduler_bootstrap import (
                    get_scheduler_unavailable_reason,
                )

                reason = get_scheduler_unavailable_reason()
                if is_serverless_mode():
                    logger.info(
                        "TaskMonitor: native scheduler not used in serverless mode; "
                        "poll GET /api/proactive/tick/{agent_id} instead."
                    )
                elif reason:
                    logger.warning(
                        "TaskMonitor: scheduler service unavailable after startup "
                        "(%s). Enable server.scheduler_enabled or set "
                        "JVSPATIAL_SCHEDULER_ENABLED=true, or use HTTP "
                        "/api/proactive/tick/{agent_id}.",
                        reason,
                    )
                else:
                    logger.warning(
                        "TaskMonitor: scheduler service unavailable after startup. "
                        "Enable server.scheduler_enabled or set "
                        "JVSPATIAL_SCHEDULER_ENABLED=true, or poll "
                        "GET /api/proactive/tick/{agent_id}."
                    )
            return

        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
        except Exception:
            pass

        try:
            from jvspatial.api.integrations.scheduler.decorators import (
                register_scheduled_tasks,
            )

            await register_scheduled_tasks(scheduler_service)
            if not scheduler_service.is_running:
                scheduler_service.start()
        except Exception as e:
            logger.error(
                "TaskMonitor: failed to register scheduler task: %s",
                e,
                exc_info=True,
            )

    async def _unregister_scheduler_task(self) -> None:
        scheduler_service = self._get_scheduler_service()
        if not scheduler_service:
            return
        try:
            if hasattr(scheduler_service, "unregister_task"):
                scheduler_service.unregister_task(SCHEDULE_TASK_ID)
        except Exception as e:
            logger.error("TaskMonitor: failed to unregister scheduler task: %s", e)

    async def tick(
        self, dry_run: bool = False, conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Scan conversations and dispatch one eligible proactive task each."""
        if not self.enabled:
            return {"dispatched": 0, "skipped": "disabled"}

        from jvagent.core.app import App, app_now_aware_utc
        from jvagent.logging.retention import purge_logs_past_retention

        app = await App.get()
        now_dt = await app_now_aware_utc(app)

        # Bound INTERACTION / DBLog growth using App.log_retention_days
        # (0 = disabled). Failures must not block proactive dispatch.
        try:
            await purge_logs_past_retention(
                retention_days=int(getattr(app, "log_retention_days", 60) or 0),
                now=now_dt,
            )
        except Exception as e:
            logger.warning("TaskMonitor: log retention purge failed: %s", e)

        query: Dict[str, Any] = {
            "tasks": {
                "$elemMatch": {
                    "task_type": PROACTIVE_TASK_TYPE,
                    "status": {"$in": ["pending", "active"]},
                    "data.spec_version": 2,
                }
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
            scoped_convs = []
            seen_conv_ids = set()
            for conv in due_convs:
                user = await conv.node(direction="in", node=User)
                if user and (
                    user.memory_id == memory.id or await memory.is_connected_to(user)
                ):
                    scoped_convs.append(conv)
                    seen_conv_ids.add(conv.id)

            # ADR-0034: resolve the interview action once so the staleness reaper
            # can look up per-skill TTL policy, then also scope in conversations
            # that hold an idle interview SKILL task but no proactive task.
            interview_action = None
            spec_lookup = None
            try:
                interview_action = await agent.get_action_by_type("InterviewAction")
                if interview_action is not None and hasattr(
                    interview_action, "_ensure_specs_loaded"
                ):
                    await interview_action._ensure_specs_loaded()
            except Exception as exc:
                logger.debug("TaskMonitor: interview action lookup failed: %s", exc)
                interview_action = None
            if interview_action is not None:

                def spec_lookup(owner: str, _ia: Any = interview_action) -> Any:
                    try:
                        return _ia._registry.get(owner)
                    except Exception:
                        return None

                iv_query: Dict[str, Any] = {
                    "tasks": {
                        "$elemMatch": {
                            "task_type": "SKILL",
                            "status": {"$in": ["active", "parked"]},
                            "data.interview_managed": True,
                        }
                    }
                }
                if conversation_id:
                    iv_query["session_id"] = conversation_id
                try:
                    for conv in await Conversation.find(iv_query):
                        if conv.id in seen_conv_ids:
                            continue
                        user = await conv.node(direction="in", node=User)
                        if user and (
                            user.memory_id == memory.id
                            or await memory.is_connected_to(user)
                        ):
                            scoped_convs.append(conv)
                            seen_conv_ids.add(conv.id)
                except Exception as exc:
                    logger.debug("TaskMonitor: interview conv scan failed: %s", exc)

            dispatched_count = 0
            semaphore = asyncio.Semaphore(max(1, int(self.max_parallel_conversations)))

            async def _dispatch_conversation(conv_id: str) -> None:
                nonlocal dispatched_count
                async with semaphore:
                    try:
                        from jvagent.memory.distributed_conversation_lock import (
                            conversation_mutation_lock,
                        )
                    except ImportError:

                        @asynccontextmanager
                        async def conversation_mutation_lock(id):  # type: ignore
                            yield

                    try:
                        async with conversation_mutation_lock(conv_id):
                            conversation = await Conversation.get(conv_id)
                            if not conversation:
                                return

                            store = TaskStore(conversation)
                            await cancel_expired_pending(store, now=now_dt)
                            await sweep_terminal_proactive(
                                store,
                                ttl_days=int(self.terminal_ttl_days or 0),
                                now=now_dt,
                            )

                            # ADR-0034: staleness reaper for idle interview tasks
                            # (nudge / abandon / expire). No-op for conversations
                            # without idle interview tasks. Runs under the same
                            # per-conversation lock (the in-flight-turn rail).
                            if interview_action is not None and not dry_run:
                                try:
                                    from jvagent.action.interview.reaper import (
                                        reap_interview_tasks,
                                    )

                                    async def _send(
                                        text: str, _conv: Any = conversation
                                    ) -> None:
                                        if hasattr(agent, "send_proactive_message"):
                                            await agent.send_proactive_message(
                                                user_id=getattr(_conv, "user_id", "")
                                                or "",
                                                content=text,
                                                channel=getattr(_conv, "channel", "")
                                                or "default",
                                                session_id=getattr(
                                                    _conv, "session_id", None
                                                ),
                                            )

                                    await reap_interview_tasks(
                                        conversation,
                                        store,
                                        spec_lookup,
                                        now_dt,
                                        send=_send,
                                    )
                                except Exception as exc:
                                    logger.debug(
                                        "TaskMonitor: interview reap failed for %s: %s",
                                        conv_id,
                                        exc,
                                    )

                            handle = pick_next_proactive_task(store, now=now_dt)
                            if handle is None:
                                return

                            if dry_run:
                                # Dry-run must have NO side effects. Calling
                                # handle.complete() here transitioned the picked
                                # task pending -> completed, which Task.transition
                                # rejects ("Cannot transition task from 'pending'")
                                # — the error was swallowed as "dispatch failed"
                                # and nothing was ever counted. Just report.
                                # AUDIT-memory (M13).
                                logger.info(
                                    "TaskMonitor dry-run: would dispatch %s",
                                    handle.id,
                                )
                                dispatched_count += 1
                                return

                            ok = await self.dispatch_one(
                                agent,
                                conversation,
                                handle,
                                store=store,
                            )
                            if ok:
                                dispatched_count += 1
                    except Exception as e:
                        logger.error(
                            "TaskMonitor: dispatch failed for %s: %s",
                            conv_id,
                            e,
                            exc_info=True,
                        )

            if scoped_convs:
                await app.initialize_actions()
                await asyncio.gather(
                    *(_dispatch_conversation(conv.id) for conv in scoped_convs),
                    return_exceptions=True,
                )

            return {
                "dispatched": dispatched_count,
                "timestamp": now_dt.isoformat(),
            }
        except Exception as e:
            logger.error("TaskMonitor tick error: %s", e, exc_info=True)
            return {"error": str(e)}

    async def dispatch_one(
        self,
        agent: Any,
        conversation: Any,
        handle: Any,
        *,
        store: Optional[TaskStore] = None,
        dry_run: bool = False,
    ) -> bool:
        """Claim and run one proactive task through the Orchestrator pipeline."""
        store = store or TaskStore(conversation)
        lease_id = uuid.uuid4().hex
        if not await store.claim_proactive(handle.id, lease_id):
            return False

        try:
            spec = ProactiveTaskSpec.from_task_handle(store.get(handle.id))
        except ValueError:
            h = store.get(handle.id)
            if h:
                await h.fail(reason="invalid proactive spec")
            return False

        utterance = f"[PROACTIVE_TASK:{handle.id}] {spec.directive}"
        response_bus = await agent.get_response_bus()
        from jvagent.action.interact.interact_walker import InteractWalker

        walker = InteractWalker(
            agent_id=agent.id,
            utterance=utterance,
            channel=spec.channel or conversation.channel or "default",
            session_id=conversation.session_id,
            user_id=conversation.user_id,
            response_bus=response_bus,
            data={
                "is_proactive": True,
                "proactive_task_id": handle.id,
                "proactive_directive": spec.directive,
                "proactive_skill": spec.skill,
            },
        )
        walker.conversation = conversation

        dispatch_error: Optional[BaseException] = None
        try:
            await walker.spawn(agent)
            interaction = walker.interaction
            if interaction is not None:
                interaction.add_parameter(walker.data, "TaskMonitor")
                interaction.add_directives([spec.directive], "TaskMonitor")
                await interaction.save()
        except Exception as exc:
            dispatch_error = exc
            interaction = getattr(walker, "interaction", None)
            logger.error(
                "TaskMonitor: orchestrator dispatch error for %s: %s",
                handle.id,
                exc,
                exc_info=True,
            )

        await finalize_proactive_task(
            store,
            handle.id,
            interaction=getattr(walker, "interaction", None),
            error=dispatch_error,
        )
        return dispatch_error is None

    async def attach_event_task(
        self,
        visitor: Any,
        handle: Any,
    ) -> None:
        """Bridge a same-turn event-eligible task into the walker."""
        await attach_proactive_to_visitor(visitor, handle)


async def attach_proactive_to_visitor(visitor: Any, handle: Any) -> None:
    """Bridge a claimed proactive task into the walker for same-turn execution."""
    spec = ProactiveTaskSpec.from_task_handle(handle)
    directive = spec.directive
    if spec.context:
        directive = f"{directive}\nCONTEXT: {spec.context}"
    data = getattr(visitor, "data", None)
    if data is None:
        visitor.data = {}
        data = visitor.data
    data["proactive_task_id"] = handle.id
    data["proactive_directive"] = spec.directive
    data["proactive_skill"] = spec.skill
    data["is_proactive"] = True
    await visitor.add_directive(directive)


if on_schedule:

    @on_schedule("every 2 minutes", task_id="system_task_monitor")
    async def _native_task_monitor_tick():
        logger.debug("Ticking TaskMonitor...")
        try:
            monitors = await TaskMonitor.find({"context.enabled": True})
            if monitors:
                await asyncio.gather(
                    *(m.tick() for m in monitors if getattr(m, "enabled", True)),
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error("Global TaskMonitor tick error: %s", e)
