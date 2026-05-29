"""Skill catalog for the Executive's Skills center (ADR-0010 / ADR-0011).

A **jvagent-native skill is an SOP overlay**, not a capability: a markdown
procedure (the SKILL.md body) that *references existing action tools by their
canonical ``namespace__tool`` name*. Execution comes from the agent's actions
(`get_tools()`); the skill only adds judgment — when to use which tool, in what
order, how to interpret results.

Discovery reuses the **pattern-neutral** resolver in
``jvagent.scaffold.skill_resolve`` (builtin ``jvagent.skills`` + app-local
``agents/<ns>/<agent>/skills/*``), so this stays isolation-clean — it imports
nothing from ``bridge`` / ``helm`` / ``cockpit``.

Self-contained Claude skill *bundles* that ship their own executable scripts
are a different substrate (sandboxed execution) — out of scope here; see
ADR-0011.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillDoc:
    """A native SOP skill: a procedure that coordinates existing tools.

    ``requires_tools`` is the skill's declared tool surface (from SKILL.md
    ``allowed-tools``) — treated as a soft dependency: the skill still
    activates if a tool is missing, but the Skills center warns so the model
    won't blindly follow a step it can't execute.
    """

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

    Best-effort: returns ``[]`` on any failure (no app root, missing agent
    fields, resolver error). ``skills_source``: ``both`` | ``local`` | ``app``
    | ``registry`` | ``builtin`` (mirrors the bridge knob; ``local``/``app``
    drop built-ins, ``registry``/``builtin`` keep built-ins).
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
        logger.debug("skills_catalog: resolver import failed: %s", exc)
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
        logger.debug("skills_catalog: bundle resolution failed: %s", exc)
        return []

    if source in ("local", "app"):
        bundles = {k: v for k, v in bundles.items() if v.get("source") != "builtin"}

    try:
        kept = apply_skill_selector(bundles, selector or "-all", denied or None)
    except Exception as exc:
        logger.debug("skills_catalog: selector failed: %s", exc)
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
