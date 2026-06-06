"""Host-provided SOP skills for embedded deployments (ADR-0012 extension).

Hosts (e.g. Integral) register sync callables that return additional
:class:`~jvagent.action.orchestrator.skills.SkillDoc` entries at runtime.
These merge into :func:`~jvagent.action.orchestrator.skills.discover_skill_docs`
after filesystem resolution. Filesystem / app-local skills win on name
collision so a host overlay cannot shadow the agent's base skill set.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List

from jvagent.action.orchestrator.skills import SkillDoc

logger = logging.getLogger(__name__)

HostSkillProvider = Callable[[Any], List[SkillDoc]]

_providers: List[HostSkillProvider] = []


def register_host_skill_provider(fn: HostSkillProvider) -> None:
    """Register a host skill provider. Safe to call multiple times."""
    if fn not in _providers:
        _providers.append(fn)


def clear_host_skill_providers() -> None:
    """Remove all registered providers (tests)."""
    _providers.clear()


def collect_host_skill_docs(agent: Any) -> List[SkillDoc]:
    """Invoke every registered provider; best-effort per provider."""
    if not _providers:
        return []
    docs: List[SkillDoc] = []
    for provider in _providers:
        try:
            batch = provider(agent)
            if batch:
                docs.extend(batch)
        except Exception as exc:
            logger.debug(
                "orchestrator.skill_providers: provider %r failed: %s",
                provider,
                exc,
            )
    return docs


__all__ = [
    "HostSkillProvider",
    "clear_host_skill_providers",
    "collect_host_skill_docs",
    "register_host_skill_provider",
]
