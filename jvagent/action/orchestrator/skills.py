"""Native SOP skill discovery for the Orchestrator (ADR-0011 / ADR-0012).

A jvagent-native skill is an SOP overlay (a SKILL.md body referencing existing
action tools by ``namespace__tool`` name), not executable capability. Discovery
reuses the pattern-neutral resolver in ``jvagent.scaffold.skill_resolve`` so this
stays isolation-clean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
    locked_in: bool = False
    metadata: dict = field(default_factory=dict)


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

    # Canonical sources: ``app`` (adjacent agents/<ns>/<agent>/skills),
    # ``library`` (built-in jvagent/skills), or ``both``. Older values are kept
    # working as aliases; ``registry`` is retired (no registry backend) and
    # treated as ``library``.
    raw = (skills_source or "both").strip().lower()
    source = {"local": "app", "builtin": "library", "registry": "library"}.get(raw, raw)
    if raw == "registry":
        logger.warning(
            "orchestrator.skills: skills_source='registry' is deprecated; "
            "using 'library' (jvagent/skills)."
        )
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

    # ``app`` keeps only adjacent skills; ``library`` keeps only built-ins
    # (resolve_merged_skill_bundles always folds in app-local, so filter here).
    if source == "app":
        bundles = {k: v for k, v in bundles.items() if v.get("source") != "builtin"}
    elif source == "library":
        bundles = {k: v for k, v in bundles.items() if v.get("source") != "app"}

    try:
        kept = apply_skill_selector(bundles, selector or "-all", denied or None)
    except Exception as exc:
        logger.debug("orchestrator.skills: selector failed: %s", exc)
        kept = bundles

    docs: List[SkillDoc] = []
    for nm, bundle in kept.items():
        body = (bundle.get("content") or "").strip()
        try:
            from jvagent.action.interview_action.procedure import (
                compose_interview_skill_body_from_bundle,
                is_interview_skill_bundle,
            )

            if is_interview_skill_bundle(bundle):
                body = compose_interview_skill_body_from_bundle(bundle)
        except Exception as exc:
            logger.debug(
                "orchestrator.skills: interview procedure compose failed for %s: %s",
                nm,
                exc,
            )
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
                locked_in=bool(bundle.get("locked_in", False)),
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

    return docs


__all__ = ["SkillDoc", "discover_skill_docs"]
