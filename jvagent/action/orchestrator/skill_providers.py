"""Host-provided SOP skills for embedded deployments (ADR-0012 / HP-08).

Legacy process-global callables remain as a shim. New hosts should register
tools/skills on :class:`~jvagent.harness.runtime.HarnessRuntime` (per
``session_id``) and serve them through ``ToolSurfaceSnapshot``. The Orchestrator
must not import host services.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List

from jvagent.action.orchestrator.skills import SkillDoc

logger = logging.getLogger(__name__)

HostSkillProvider = Callable[[Any], List[SkillDoc]]

_providers: List[HostSkillProvider] = []


def register_host_skill_provider(fn: HostSkillProvider) -> None:
    """Register a legacy host skill provider. Prefer HostCapabilityProvider."""
    if fn not in _providers:
        _providers.append(fn)


def clear_host_skill_providers() -> None:
    """Remove all registered providers (tests)."""
    _providers.clear()


def collect_host_skill_docs(agent: Any) -> List[SkillDoc]:
    """Invoke every registered provider; best-effort per provider.

    Snapshot-scoped host skills (HP-08) are merged from the admitted
    ToolSurfaceSnapshot when a turn cache is bound.
    """
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
    try:
        from jvagent.action.orchestrator.turn_cache import get_turn_cache

        turn = get_turn_cache() or {}
        snap = turn.get("snapshot")
        if snap is not None:
            from jvagent.harness.runtime import get_runtime

            existing = {d.name for d in docs}
            for key in getattr(snap, "host_skill_keys", ()) or ():
                if key and key not in existing:
                    materialization = get_runtime().host_skill_materialization(
                        snap.caller.session_id, key
                    )
                    if materialization is None:
                        logger.warning(
                            "host skill %r has no registered materialization", key
                        )
                        continue
                    docs.append(
                        SkillDoc(
                            name=key,
                            description=f"Host skill {key}",
                            body=materialization.body,
                            source="host",
                            spec=materialization.spec,
                            digest=materialization.digest,
                        )
                    )
    except Exception as exc:
        logger.debug("orchestrator.skill_providers: snapshot merge failed: %s", exc)
    return docs


__all__ = [
    "HostSkillProvider",
    "clear_host_skill_providers",
    "collect_host_skill_docs",
    "register_host_skill_provider",
]
