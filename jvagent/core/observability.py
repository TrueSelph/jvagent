"""Pluggable observability hooks for metrics and tracing integrations."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ObservabilityHook(Protocol):
    """Emit structured events (usage, latency, costs) without coupling core code to vendors."""

    async def on_event(self, name: str, payload: Dict[str, Any]) -> None:
        """Handle one named event with arbitrary JSON-serializable payload."""
        ...


_hooks: List[ObservabilityHook] = []


def register_observability_hook(hook: ObservabilityHook) -> None:
    """Append a hook (idempotent registration left to callers)."""
    if hook not in _hooks:
        _hooks.append(hook)


def clear_observability_hooks() -> None:
    """Remove all hooks (primarily for tests)."""
    _hooks.clear()


async def emit_observability_event(name: str, payload: Dict[str, Any]) -> None:
    """Fan-out one event to all registered hooks; failures are logged, not raised."""
    for hook in list(_hooks):
        try:
            await hook.on_event(name, payload)
        except Exception as exc:
            logger.debug("Observability hook error for %s: %s", name, exc)
