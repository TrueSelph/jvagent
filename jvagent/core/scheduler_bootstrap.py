"""Bootstrap jvspatial SchedulerService for TaskMonitor and other @on_schedule hooks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_bound_server: Optional[Any] = None
_standalone_scheduler: Optional[Any] = None
_last_unavailable_reason: Optional[str] = None


def get_scheduler_unavailable_reason() -> Optional[str]:
    return _last_unavailable_reason


def app_has_task_monitor(app_root: Optional[str]) -> bool:
    """Return True when any agent.yaml under *app_root* enables task_monitor."""
    if not app_root:
        return False
    agents_dir = Path(app_root) / "agents"
    if not agents_dir.is_dir():
        return False
    for agent_yaml in agents_dir.rglob("agent.yaml"):
        try:
            if "jvagent/task_monitor" in agent_yaml.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _import_scheduler() -> Optional[tuple]:
    global _last_unavailable_reason
    try:
        from jvspatial.api.integrations.scheduler.decorators import (
            register_scheduled_tasks,
            set_default_scheduler,
        )
        from jvspatial.api.integrations.scheduler.scheduler import (
            SchedulerConfig,
            SchedulerService,
        )

        return (
            SchedulerService,
            SchedulerConfig,
            register_scheduled_tasks,
            set_default_scheduler,
        )
    except ImportError as exc:
        _last_unavailable_reason = (
            f"scheduler dependencies unavailable ({exc}); "
            "install jvspatial with scheduler support"
        )
        logger.debug("scheduler_bootstrap: import failed: %s", exc)
        return None


async def ensure_scheduler_for_server(
    server: Any,
    *,
    start: bool = True,
) -> Optional[Any]:
    """Create, register, and optionally start SchedulerService on *server*."""
    global _bound_server, _last_unavailable_reason

    imported = _import_scheduler()
    if imported is None:
        return None

    (
        SchedulerService,
        SchedulerConfig,
        register_scheduled_tasks,
        set_default_scheduler,
    ) = imported

    from jvspatial.runtime.serverless import is_serverless_mode

    svc = getattr(server, "scheduler_service", None)
    if svc is None:
        interval = int(getattr(server.config, "scheduler_interval", 1) or 1)
        svc = SchedulerService(
            config=SchedulerConfig(enabled=True, interval=interval),
            graph_context=getattr(server, "_graph_context", None),
        )
        server.scheduler_service = svc

    _bound_server = server
    set_default_scheduler(svc)

    try:
        await register_scheduled_tasks(svc)
    except Exception as exc:
        _last_unavailable_reason = f"failed to register scheduled tasks: {exc}"
        logger.error("scheduler_bootstrap: register failed: %s", exc, exc_info=True)
        return None

    if start and not is_serverless_mode():
        if not svc.is_running:
            svc.start()
        logger.info("jvagent scheduler started (interval=%ss)", svc.config.interval)
    elif start:
        logger.info(
            "jvagent scheduler registered but not started (serverless mode); "
            "use GET /api/proactive/tick/{agent_id} for proactive ticks"
        )

    _last_unavailable_reason = None
    return svc


async def ensure_standalone_scheduler(*, start: bool = True) -> Optional[Any]:
    """Process-level scheduler for bootstrap-only paths without a Server."""
    global _standalone_scheduler, _last_unavailable_reason

    if _standalone_scheduler is not None:
        return _standalone_scheduler

    imported = _import_scheduler()
    if imported is None:
        return None

    (
        SchedulerService,
        SchedulerConfig,
        register_scheduled_tasks,
        set_default_scheduler,
    ) = imported

    from jvspatial.runtime.serverless import is_serverless_mode

    svc = SchedulerService(config=SchedulerConfig(enabled=True, interval=60))
    _standalone_scheduler = svc
    set_default_scheduler(svc)

    try:
        await register_scheduled_tasks(svc)
    except Exception as exc:
        _last_unavailable_reason = f"failed to register scheduled tasks: {exc}"
        logger.error("scheduler_bootstrap: standalone register failed: %s", exc)
        return None

    if start and not is_serverless_mode() and not svc.is_running:
        svc.start()

    _last_unavailable_reason = None
    return svc


def get_bound_scheduler_server() -> Optional[Any]:
    return _bound_server


def resolve_scheduler_service(server: Optional[Any] = None) -> Optional[Any]:
    """Best-effort scheduler lookup for TaskMonitor."""
    from jvspatial.api.context import get_current_server
    from jvspatial.api.integrations.scheduler.decorators import get_default_scheduler

    server = server or _bound_server or get_current_server()
    if server is not None:
        svc = getattr(server, "scheduler_service", None)
        if svc is not None:
            return svc
    return get_default_scheduler() or _standalone_scheduler
