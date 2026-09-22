"""Shared importer for jvagent's @endpoint-decorated route modules.

Importing each module in this helper triggers ``@endpoint`` decorator
side effects, which register the routes either:

* immediately on the current jvspatial ``Server`` (if one is already
  installed via ``set_current_server``), or
* into jvspatial's deferred-endpoint registry, which gets flushed when
  the host instantiates its ``Server``.

Both the standalone CLI (``jvagent.cli.server_config``) and the embed
surface (``jvagent.embed``) call into this single function so the set of
"first-party" endpoint modules stays in lockstep across run modes.
"""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)

_NOTIFY_PATH = "/artifact_handler_action/notify/{agent_id}"
_NOTIFY_PATH_MARKER = "artifact_handler_action/notify"


def _live_notify_routes(app: Any) -> List[str]:
    """Return live FastAPI routes that look like the notify webhook."""
    found: List[str] = []
    for route in getattr(app, "routes", None) or []:
        path = str(getattr(route, "path", "") or "")
        if _NOTIFY_PATH_MARKER not in path:
            continue
        methods = sorted(str(m) for m in (getattr(route, "methods", None) or []))
        found.append(f"{path}[{','.join(methods)}]" if methods else path)
    return found


def import_jvagent_endpoint_modules() -> None:
    """Import every first-party endpoint module in jvagent."""
    from jvagent.action import endpoints as _action_endpoints  # noqa: F401
    from jvagent.action.artifact_handler_interact_action import (  # noqa: F401
        endpoints as _ah_endpoints,
    )
    from jvagent.core import endpoints as _core_endpoints  # noqa: F401
    from jvagent.logging import endpoints as _logging_endpoints  # noqa: F401

    cfg = (
        getattr(
            _ah_endpoints.artifact_handler_notify, "_jvspatial_endpoint_config", None
        )
        or {}
    )
    logger.warning(
        "artifact_handler notify: endpoints imported path=%s methods=%s "
        "webhook=%s webhook_auth=%s",
        cfg.get("path") or _NOTIFY_PATH,
        list(cfg.get("methods") or ["POST"]),
        cfg.get("webhook"),
        cfg.get("webhook_auth"),
    )


def remount_artifact_handler_notify_if_app_built(server: Any) -> None:
    """Mount notify on the live FastAPI app when ``get_app()`` already ran.

    Function ``@endpoint`` registration after ``get_app()`` updates the
    registry only; Lambda/LWA then 404s. No-op when ``server.app`` is still
    None (normal CLI: import happens before ``get_app()``).
    """
    app = getattr(server, "app", None)
    if app is None:
        logger.warning(
            "artifact_handler notify: remount skipped app_built=False "
            "(route should land on later get_app())"
        )
        return
    live_before = _live_notify_routes(app)
    remount = getattr(server, "_register_function_dynamically", None)
    if not callable(remount):
        logger.warning(
            "artifact_handler notify: live FastAPI app already built but "
            "Server has no _register_function_dynamically; notify may 404 "
            "live_routes=%s",
            live_before or "(none)",
        )
        return

    from jvagent.action.artifact_handler_interact_action.endpoints import (
        artifact_handler_notify,
    )

    cfg = getattr(artifact_handler_notify, "_jvspatial_endpoint_config", None) or {}
    registry = getattr(server, "_endpoint_registry", None)
    info = None
    if registry is not None:
        getter = getattr(registry, "get_function_info", None)
        if callable(getter):
            info = getter(artifact_handler_notify)

    wrapped = None
    path = _NOTIFY_PATH
    methods = ["POST"]
    if info is not None:
        path = info.path or path
        methods = list(info.methods or methods) or methods
        route_config = (info.kwargs or {}).get("route_config") or {}
        wrapped = route_config.get("endpoint")
    else:
        path = cfg.get("path") or path
        methods = list(cfg.get("methods") or methods) or methods

    logger.warning(
        "artifact_handler notify: remounting path=%s methods=%s "
        "registry=%s live_routes_before=%s",
        path,
        methods,
        info is not None,
        live_before or "(none)",
    )
    try:
        remount(
            wrapped or artifact_handler_notify,
            path,
            methods,
            source_obj=artifact_handler_notify,
            auth=cfg.get("auth_required", False),
            permissions=cfg.get("permissions") or [],
            roles=cfg.get("roles") or [],
            response=cfg.get("response"),
            webhook=cfg.get("webhook", True),
            webhook_auth=cfg.get("webhook_auth", "api_key"),
        )
    except Exception:
        logger.warning(
            "artifact_handler notify: failed to remount on live FastAPI app "
            "path=%s live_routes=%s",
            path,
            _live_notify_routes(app) or "(none)",
            exc_info=True,
        )
        return

    live_after = _live_notify_routes(app)
    logger.warning(
        "artifact_handler notify: remount finished path=%s live_routes=%s",
        path,
        live_after or "(none)",
    )


__all__ = [
    "import_jvagent_endpoint_modules",
    "remount_artifact_handler_notify_if_app_built",
]
