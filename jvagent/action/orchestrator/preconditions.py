"""Precondition registry (ADR-0026): domain-agnostic named predicates.

A consumer app registers named checks at bootstrap; a skill's declarative
``requires-tasks`` references them by name. The harness never knows what a
precondition *means* — only its boolean result. This is the seam that keeps the
work-stack service framework-agnostic: no domain term lives in core.

    register_precondition("session_valid", lambda v: consumer_check(v))

Then in a skill's SKILL.md frontmatter::

    requires-tasks:
      - when: session_valid
        push: some_prerequisite_skill
        seed_from: [utterance]

The names above are placeholders chosen by the consumer; core never sees them
spelled out — only their boolean results flow through ``evaluate_precondition``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# (visitor) -> bool | Awaitable[bool]
Predicate = Callable[[Any], Any]

_REGISTRY: Dict[str, Predicate] = {}


def register_precondition(name: str, predicate: Predicate) -> None:
    """Bind a precondition name to a predicate. Idempotent (last write wins)."""
    key = str(name or "").strip()
    if not key:
        raise ValueError("precondition name is required")
    if not callable(predicate):
        raise ValueError("precondition predicate must be callable")
    _REGISTRY[key] = predicate


def precondition_registered(name: str) -> bool:
    return str(name or "").strip() in _REGISTRY


async def evaluate_precondition(name: str, visitor: Any) -> bool:
    """True when the precondition is satisfied.

    Fails OPEN: an unregistered name or a raising predicate is treated as satisfied
    (logged loudly). This avoids deadlocking the agent on a config typo; an
    unregistered gate is a developer error visible in the logs, not a silent loop.
    Re-push protection (the push records which preconditions it has already acted on)
    means a satisfied result here simply lets the gated skill proceed.
    """
    key = str(name or "").strip()
    predicate = _REGISTRY.get(key)
    if predicate is None:
        logger.warning("precondition %r is not registered; treating as satisfied", key)
        return True
    try:
        result = predicate(visitor)
        if hasattr(result, "__await__"):
            result = await result
        return bool(result)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("precondition %r raised %s; treating as satisfied", key, exc)
        return True


def clear_preconditions() -> None:
    """Test helper — reset the registry."""
    _REGISTRY.clear()
