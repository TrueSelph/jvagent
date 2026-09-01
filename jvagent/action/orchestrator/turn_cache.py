"""Per-turn memoization for the Orchestrator's repeated graph reads.

``Actions.get_all_actions()`` walks the agent's action subgraph breadth-first
with a database read per node. It is not cheap — ~80 ms on the example agent —
and the orchestrator calls it four to five times in a single turn from
independent call sites (parameter accumulation, routable-flow resolution, tool
assembly, the loop body), each of which legitimately just wants "the enabled
actions". Re-reading them cost ~400 ms of the turn's wall clock before the model
was even called.

The action set cannot change *within* one turn, so it is memoized for exactly
that scope. A :class:`~contextvars.ContextVar` is the right scope holder rather
than an instance attribute: the Action node is a shared singleton, so an
instance attribute would leak one request's surface into a concurrent request's
turn, whereas a ContextVar is bound per asyncio task.

Outside a bound turn (a direct unit-test call, a background task) the cache is
absent and every read goes straight through — no behavioural difference.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Dict, Iterator, Optional

_turn_cache: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "jvagent_orchestrator_turn_cache", default=None
)


@contextlib.contextmanager
def bind_turn_cache() -> Iterator[Dict[str, Any]]:
    """Open a per-turn memo scope; reset on exit so nothing outlives the turn."""
    cache: Dict[str, Any] = {}
    token = _turn_cache.set(cache)
    try:
        yield cache
    finally:
        _turn_cache.reset(token)


def get_turn_cache() -> Optional[Dict[str, Any]]:
    """The active turn's memo dict, or None when no turn scope is bound."""
    return _turn_cache.get()


_PROMPT_SECTIONS_KEY = "prompt_sections"


def get_prompt_cache() -> Dict[str, Any]:
    """Per-turn orchestrator prompt sections (ContextVar-scoped via turn cache)."""
    turn = get_turn_cache()
    if turn is None:
        return {}
    sections = turn.get(_PROMPT_SECTIONS_KEY)
    return dict(sections) if isinstance(sections, dict) else {}


def set_prompt_cache(sections: Dict[str, Any]) -> None:
    """Store orchestrator prompt sections for the active turn."""
    turn = get_turn_cache()
    if turn is not None:
        turn[_PROMPT_SECTIONS_KEY] = dict(sections)


def update_prompt_cache(key: str, value: Any) -> None:
    """Update one prompt section in the active turn cache."""
    turn = get_turn_cache()
    if turn is not None:
        sections = turn.setdefault(_PROMPT_SECTIONS_KEY, {})
        if isinstance(sections, dict):
            sections[key] = value


__all__ = [
    "bind_turn_cache",
    "get_prompt_cache",
    "get_turn_cache",
    "set_prompt_cache",
    "update_prompt_cache",
]
