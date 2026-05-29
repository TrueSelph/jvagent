"""Native SOP skill discovery for the SkillExecutive (ADR-0011 / ADR-0012).

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
    source: str = "app"
    metadata: dict = field(default_factory=dict)


def discover_skill_docs(
    agent: Any,
    *,
    skills_source: str = "both",
    selector: Any = "-all",
    denied: Optional[List[str]] = None,
) -> List[SkillDoc]:
    """Discover native SOP skills for ``agent`` via the neutral resolver.

    Best-effort: returns ``[]`` on any failure. ``skills_source``:
    ``both`` | ``local`` | ``app`` | ``registry`` | ``builtin``.
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
        logger.debug("skill_executive.skills: resolver import failed: %s", exc)
        return []

    app_root = get_app_root()
    namespace = getattr(agent, "namespace", None)
    name = getattr(agent, "name", None)
    if not app_root or not namespace or not name:
        return []

    source = (skills_source or "both").strip().lower()
    include_builtin = source in ("both", "builtin", "registry")
    try:
        bundles = resolve_merged_skill_bundles(
            str(app_root), namespace, name, include_builtin=include_builtin
        )
    except Exception as exc:
        logger.debug("skill_executive.skills: bundle resolution failed: %s", exc)
        return []

    if source in ("local", "app"):
        bundles = {k: v for k, v in bundles.items() if v.get("source") != "builtin"}

    try:
        kept = apply_skill_selector(bundles, selector or "-all", denied or None)
    except Exception as exc:
        logger.debug("skill_executive.skills: selector failed: %s", exc)
        kept = bundles

    docs: List[SkillDoc] = []
    for nm, bundle in kept.items():
        docs.append(
            SkillDoc(
                name=nm,
                description=(bundle.get("description") or "").strip(),
                body=(bundle.get("content") or "").strip(),
                requires_tools=tuple(bundle.get("allowed_tools") or ()),
                source=bundle.get("source", "app"),
                metadata=bundle.get("metadata") or {},
            )
        )
    return docs


__all__ = ["SkillDoc", "discover_skill_docs"]
