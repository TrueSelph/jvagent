"""Public embed surface for hosting jvagent inside another jvspatial process.

This module is the supported entry point for running jvagent as a library —
i.e. inside a host application that already owns the jvspatial ``Server`` and
its FastAPI app — instead of running jvagent's own CLI server.

The host is responsible for:

1. Constructing the jvspatial ``Server`` (or otherwise initializing the
   default jvspatial context with a ``Database``).
2. Calling :func:`bootstrap` once during application startup, after the
   default jvspatial context exists.
3. Mounting an interaction endpoint (or other surface) that calls into
   jvagent's interact subsystem with a host-resolved ``user_id``.

Stability contract
------------------

The names exported here (``bootstrap``, ``shutdown``, ``run_index_migration``,
``run_app_startup``) are part of jvagent's public, semver-tracked surface.
Internal helpers (anything starting with ``_``) are not.

Other modules under :mod:`jvagent` are considered internal until promoted
here. Hosts depending on internal modules must pin to an exact jvagent
version.
"""

from jvagent.embed.bootstrap import (
    Server,
    bootstrap,
    register_jvagent_endpoints_on_host,
    run_app_startup,
    run_index_migration,
    shutdown,
)
from jvagent.embed.interact import (
    cancel_interact,
    delete_conversation,
    enqueue_proactive_task,
    ensure_user,
    get_agent_id_by_name,
    interact,
    interact_stream,
    list_agents,
    list_user_conversations,
    purge_user,
)

__all__ = [
    "Server",
    "bootstrap",
    "shutdown",
    "run_index_migration",
    "run_app_startup",
    "register_jvagent_endpoints_on_host",
    "interact",
    "interact_stream",
    "cancel_interact",
    "list_agents",
    "get_agent_id_by_name",
    "ensure_user",
    "list_user_conversations",
    "delete_conversation",
    "purge_user",
    "enqueue_proactive_task",
]
