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
from typing import Any

logger = logging.getLogger(__name__)

_NOTIFY_PATH = "/artifact_handler_action/notify/{agent_id}"


def import_jvagent_endpoint_modules() -> None:
    """Import every first-party endpoint module in jvagent."""
    from jvagent.action import endpoints as _action_endpoints  # noqa: F401
    from jvagent.action.artifact_handler_interact_action import (  # noqa: F401
        endpoints as _ah_endpoints,
    )
    from jvagent.core import endpoints as _core_endpoints  # noqa: F401
    from jvagent.logging import endpoints as _logging_endpoints  # noqa: F401


def remount_artifact_handler_notify_if_app_built(server: Any) -> None:
    """Mount notify on the live FastAPI app when ``get_app()`` already ran.

    Function ``@endpoint`` registration after ``get_app()`` updates the
    registry only; Lambda/LWA then 404s. No-op when ``server.app`` is still
    None (normal CLI: import happens before ``get_app()``).
    """
    if getattr(server, "app", None) is None:
        return
    remount = getattr(server, "_register_function_dynamically", None)
    if not callable(remount):
        logger.warning(
            "artifact_handler notify: live FastAPI app already built but "
            "Server has no _register_function_dynamically; notify may 404"
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
            "artifact_handler notify: failed to remount on live FastAPI app",
            exc_info=True,
        )


__all__ = [
    "import_jvagent_endpoint_modules",
    "remount_artifact_handler_notify_if_app_built",
]
