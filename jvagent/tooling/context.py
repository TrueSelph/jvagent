"""Host-established identity context shared by Action execution adapters."""

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Any, Iterator, Optional


@dataclass(frozen=True)
class ToolDispatchContext:
    """Immutable identity bundle exposed to tool closures.

    Carries the small set of caller-identity fields tools actually need (e.g.
    MCP filesystem dispatch needs a per-user subprocess key). Frozen so a
    rogue tool cannot mutate the bundle and surprise its sibling tools, and
    narrow so tools cannot reach into the live walker / engine state.
    """

    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    interaction_id: Optional[str] = None
    channel: Optional[str] = None


# Per-task host context, bound by the native Action adapter (or transitional
# executor) and reset after execution. Modular Actions import this service
# directly rather than importing an execution engine for caller identity.
_dispatch_context_var: contextvars.ContextVar[Optional[ToolDispatchContext]] = (
    contextvars.ContextVar("jvagent_tool_dispatch_context", default=None)
)
# Backwards-compat: some tool closures and tests still want the live visitor
# (e.g. for ``visitor.tasks``). New callers should prefer
# ``get_dispatch_context()`` and only fall back to this when they truly need
# mutable engine state.
_dispatch_visitor_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "jvagent_tool_dispatch_visitor", default=None
)


def get_dispatch_context() -> Optional[ToolDispatchContext]:
    """Return the immutable identity bundle for the current tool dispatch."""
    return _dispatch_context_var.get()


def get_tool_visitor() -> Optional[Any]:
    """Return the live InteractWalker for the current tool dispatch batch."""
    return _dispatch_visitor_var.get()


def require_tool_visitor() -> Any:
    """Return the host visitor only while its authenticated scope is unchanged."""
    visitor = get_tool_visitor()
    context = get_dispatch_context()
    if (
        visitor is None
        or context is None
        or not all(
            (
                context.agent_id,
                context.user_id,
                context.session_id,
                context.interaction_id,
            )
        )
    ):
        raise ValueError("Visitor-bound tools require complete host context")
    interaction = getattr(visitor, "interaction", None)
    if (
        _build_context_from_visitor(visitor) != context
        or getattr(interaction, "user_id", None) != context.user_id
        or getattr(interaction, "session_id", None) != context.session_id
    ):
        raise ValueError("Visitor-bound tool scope changed after host admission")
    return visitor


@contextlib.contextmanager
def bind_dispatch_context(visitor: Optional[Any]) -> Iterator[None]:
    """Bind the visitor-derived dispatch context for direct tool execution.

    The native Action adapter and transitional executor bind this service so
    context-aware tools route to the admitted caller. A direct tool caller
    must establish the same host context; a model cannot supply it.
    """
    ctx = _build_context_from_visitor(visitor)
    ctx_token = _dispatch_context_var.set(ctx)
    visitor_token = _dispatch_visitor_var.set(visitor)
    try:
        yield
    finally:
        _dispatch_visitor_var.reset(visitor_token)
        _dispatch_context_var.reset(ctx_token)


def _build_context_from_visitor(
    visitor: Optional[Any],
) -> Optional[ToolDispatchContext]:
    """Snapshot caller-identity fields off *visitor* into a frozen context."""
    if visitor is None:
        return None
    agent = getattr(visitor, "_agent", None)
    return ToolDispatchContext(
        agent_id=str(getattr(agent, "id", "") or "") or None,
        user_id=getattr(visitor, "user_id", None),
        session_id=getattr(visitor, "session_id", None),
        interaction_id=getattr(getattr(visitor, "interaction", None), "id", None),
        channel=getattr(visitor, "channel", None),
    )
