"""Core endpoints package.

Importing this package registers every ``@endpoint`` decorator in the
submodules with the FastAPI server.

Submodules group routes by domain:

- ``agents``       — ``/agents/{id}`` GET / PUT / DELETE and ``/agents`` list
- ``status``       — ``/status``
- ``conversation`` — ``/agents/{id}/conversations/{user}/{session}`` DELETE
- ``app``          — ``/app/update_mode``
- ``graph_repair`` — ``/graph/repair`` POST / state / abort

Names previously available as attributes on ``jvagent.core.endpoints`` are
re-exported below for backwards compatibility (tests and any caller doing
``from jvagent.core import endpoints; endpoints.list_agents(...)``).
"""

from jvagent.core.agent import Agent  # re-export for tests using ``endpoints.Agent``
from jvagent.core.endpoints import (  # noqa: F401 — register routes on import
    agents,
    app,
    conversation,
    graph_repair,
    status,
)
from jvagent.core.endpoints.agents import (
    delete_agent,
    get_agent,
    list_agents,
    update_agent,
)
from jvagent.core.endpoints.app import put_app_update_mode
from jvagent.core.endpoints.conversation import delete_conversation
from jvagent.core.endpoints.graph_repair import (
    graph_repair_abort,
    graph_repair_state,
    repair_graph,
)
from jvagent.core.endpoints.status import get_status

__all__ = [
    "Agent",
    "delete_agent",
    "delete_conversation",
    "get_agent",
    "get_status",
    "graph_repair_abort",
    "graph_repair_state",
    "list_agents",
    "put_app_update_mode",
    "repair_graph",
    "update_agent",
]
