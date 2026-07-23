"""Native SOP skill discovery for the Orchestrator (ADR-0011 / ADR-0012).

A jvagent-native skill is an SOP overlay (a SKILL.md body referencing existing
action tools by ``namespace__tool`` name), not executable capability. Discovery
reuses the pattern-neutral resolver in ``jvagent.scaffold.skill_resolve`` so this
stays isolation-clean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SKILL_DISCOVERY_CACHE: Dict[tuple, List["SkillDoc"]] = {}


def clear_skill_discovery_cache() -> None:
    """Drop cached skill discovery results (tests / agent config changes)."""
    _SKILL_DISCOVERY_CACHE.clear()


@dataclass(frozen=True)
class SkillDoc:
    """A native SOP skill: a procedure that coordinates existing tools."""

    name: str
    description: str
    body: str
    requires_tools: Tuple[str, ...] = ()
    requires_actions: Tuple[str, ...] = ()
    source: str = "app"
    directory: str = ""
    spec: str = "jv"
    always_active: bool = False
    task_lock: bool = False
    # Secondary capabilities permitted WHILE this skill holds the turn-lock:
    # tool-name globs and/or non-locking skill names (e.g. an FAQ). Lets a locked
    # interview field a side question, then return to its pending step.
    lock_companions: Tuple[str, ...] = ()
    # Declarative prerequisites (ADR-0026): each entry is
    # ``{"when": <precondition name>, "push": <skill>, "seed_from": [...]}``. When a
    # precondition is unmet at activation, the harness pushes the named prerequisite
    # task and the gated skill waits — generic, domain-agnostic gating.
    requires_tasks: Tuple[dict, ...] = ()
    extends: Optional[str] = None
    # Per-channel gating (ADR-0032). ``allowed_channels``/``denied_channels``
    # restrict where the skill is surfaced; ``deny_access_directive`` is the
    # message the orchestrator relays to the user when the skill is hidden on
    # the current channel. Empty tuples = no channel restriction.
    allowed_channels: Tuple[str, ...] = ()
    denied_channels: Tuple[str, ...] = ()
    deny_access_directive: str = ""
    metadata: dict = field(default_factory=dict)


def _parse_requires_tasks(raw: Any) -> Tuple[dict, ...]:
    """Normalize ``requires-tasks`` frontmatter into ``{when, push, seed_from}``
    entries (ADR-0026). Tolerant: drops malformed entries."""
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    out: List[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        when = str(item.get("when") or "").strip()
        push = str(item.get("push") or "").strip()
        if not when or not push:
            continue
        seed_from = item.get("seed_from") or []
        if isinstance(seed_from, str):
            seed_from = [seed_from]
        out.append(
            {
                "when": when,
                "push": push,
                "seed_from": [str(s) for s in seed_from if s],
            }
        )
    return tuple(out)


def _validate_preconditions(skill_name: str, requires_tasks: Tuple[dict, ...]) -> None:
    """Startup validation: warn loudly when a skill declares unregistered preconditions.
    
    Runtime fail-open behavior (evaluate_precondition returns True on unknown names)
    is unchanged; this is an early warning at skill load time.
    """
    if not requires_tasks:
        return
    try:
        from jvagent.action.orchestrator.preconditions import precondition_registered
    except Exception:  # pragma: no cover
        return
    
    for entry in requires_tasks:
        when = entry.get("when") or ""
        if when and not precondition_registered(when):
            logger.warning(
                "Skill %r declares precondition %r which is not registered. "
                "Runtime will fail open (treat as satisfied). Register it via "
                "register_precondition() at app bootstrap.",
                skill_name,
                when,
            )


def discover_skill_docs(
    agent: Any,
    *,
    skills_source: str = "both",
    selector: Any = "-all",
    denied: Optional[List[str]] = None,
) -> List[SkillDoc]:
    """Discover native SOP skills for ``agent`` via the neutral resolver.

    Best-effort: returns ``[]`` on any failure. ``skills_source`` is one of
    ``app`` (adjacent ``agents/<ns>/<agent>/skills``), ``library`` (built-in
    ``jvagent/skills``), or ``both`` (default). Aliases: ``local``→``app``,
    ``builtin``→``library``; ``registry`` is retired (treated as ``library``).
    ``selector`` is ``-all`` or a list of skill-name patterns (fnmatch).
    """
    if agent is None:
        return []
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.scaffold.skill_resolve import (
            apply_skill_selector,
            resolve_merged_skill_bundles,
        )
    except Exception as exc:  # pragma: no cover - import wiring
        logger.debug("orchestrator.skills: resolver import failed: %s", exc)
        return []

    app_root = get_app_root()
    namespace = getattr(agent, "namespace", None)
    name = getattr(agent, "name", None)
    if not app_root or not namespace or not name:
        return []

    cache_key = (
        str(app_root),
        str(namespace),
        str(name),
        (skills_source or "both").strip().lower(),
        repr(selector or "-all"),
        tuple(denied or ()),
    )
    cached_docs = _SKILL_DISCOVERY_CACHE.get(cache_key)
    if cached_docs is not None:
        return list(cached_docs)

    # Canonical sources: ``app``, ``library``, or ``both``.
    raw = (skills_source or "both").strip().lower()
    source = raw
    if source not in ("app", "library", "both"):
        logger.debug(
            "orchestrator.skills: unknown skills_source %r; defaulting to 'both'",
            raw,
        )
        source = "both"

    include_builtin = source in ("both", "library")
    try:
        bundles = resolve_merged_skill_bundles(
            str(app_root), namespace, name, include_builtin=include_builtin
        )
    except Exception as exc:
        logger.debug("orchestrator.skills: bundle resolution failed: %s", exc)
        return []

    # ``app`` — app-local pure + action overlays; ``library`` — built-in pure +
    # core action skills. ``both`` keeps the full merged set.
    if source == "app":
        bundles = {k: v for k, v in bundles.items() if v.get("source") in ("app", None)}
    elif source == "library":
        bundles = {
            k: v
            for k, v in bundles.items()
            if v.get("source") in ("builtin", "action", None)
        }

    try:
        kept = apply_skill_selector(bundles, selector or "-all", denied or None)
    except Exception as exc:
        logger.debug("orchestrator.skills: selector failed: %s", exc)
        kept = bundles

    docs: List[SkillDoc] = []
    for nm, bundle in kept.items():
        body = (bundle.get("content") or "").strip()
        requires_tasks_tuple = _parse_requires_tasks(
            bundle.get("requires_tasks") or bundle.get("requires-tasks")
        )
        # Validate precondition names (fail open at runtime, warn at load).
        _validate_preconditions(nm, requires_tasks_tuple)
        docs.append(
            SkillDoc(
                name=nm,
                description=(bundle.get("description") or "").strip(),
                body=body,
                requires_tools=tuple(bundle.get("allowed_tools") or ()),
                requires_actions=tuple(bundle.get("requires_actions") or ()),
                source=bundle.get("source", "app"),
                directory=str(bundle.get("dir") or ""),
                spec=str(bundle.get("spec") or "jv"),
                always_active=bool(bundle.get("always_active", False)),
                task_lock=bool(bundle.get("task_lock", False)),
                lock_companions=tuple(bundle.get("lock_companions") or ()),
                requires_tasks=requires_tasks_tuple,
                extends=bundle.get("extends") or None,
                allowed_channels=tuple(bundle.get("allowed_channels") or ()),
                denied_channels=tuple(bundle.get("denied_channels") or ()),
                deny_access_directive=str(bundle.get("deny_access_directive") or ""),
                metadata=bundle.get("metadata") or {},
            )
        )

    # Host overlay (embedded deployments): merge after filesystem resolution.
    # Filesystem names take precedence — base tier cannot be shadowed.
    try:
        from jvagent.action.orchestrator.skill_providers import collect_host_skill_docs

        host_docs = collect_host_skill_docs(agent)
    except Exception as exc:
        logger.debug("orchestrator.skills: host provider merge failed: %s", exc)
        host_docs = []

    if host_docs:
        existing = {d.name for d in docs}
        for hd in host_docs:
            if hd.name not in existing:
                docs.append(hd)
                existing.add(hd.name)

    _SKILL_DISCOVERY_CACHE[cache_key] = list(docs)
    return docs


__all__ = ["SkillDoc", "clear_skill_discovery_cache", "discover_skill_docs"]
