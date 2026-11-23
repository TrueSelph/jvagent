"""Orchestrator pattern (ADR-0012).

A single model-driven orchestrator (``OrchestratorInteractAction``, weight
``-200``) that runs the whole turn inside one ``execute()`` call: it curates
routable IAs out of the walker queue, then runs a bounded think-act-observe loop
over a unified tool surface (action tools, anchored IAs-as-tools, persona
``reply``/``respond`` tools, core tools, and skill meta-tools), with a
progressive-disclosure tool catalog to keep the prompt slim. An in-progress flow
is surfaced as routable context and continues when the model selects its tool.

Routing is tool selection; turn-lock is an emergent flow property. See
``.planning/adr/0012-skill-executive-architecture.md``.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )


def __getattr__(name: str) -> Any:
    """Keep the Action export without loading its engine for shared metadata."""
    if name == "OrchestratorInteractAction":
        import_module("jvagent.action.orchestrator.endpoints")
        value = getattr(
            import_module("jvagent.action.orchestrator.orchestrator_interact_action"),
            name,
        )
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["OrchestratorInteractAction"]
