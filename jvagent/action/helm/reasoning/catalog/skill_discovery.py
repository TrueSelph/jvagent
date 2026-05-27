"""Skill discovery utilities for the engine (self-contained, no agent_interact imports)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, List, Optional

from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.registry.shim import EngineVisitorShim

logger = logging.getLogger(__name__)


def always_active_from_skill_dir(dir_path: str) -> bool:
    """Return True if SKILL.md frontmatter sets ``always-active: true``."""
    p = Path(dir_path) / "SKILL.md"
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.lstrip().startswith("---"):
        return False
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not m:
        return False
    for line in m.group(1).splitlines():
        if line.strip().lower().startswith("always-active:"):
            val = line.split(":", 1)[1].strip().lower()
            return val in ("true", "yes", "1")
    return False


async def list_always_active_skill_names(
    action: Any,
    agent: Any,
    conversation: Any,
) -> List[str]:
    """Skill names that should stay pre-registered (router + explicit always-active)."""
    try:
        shim = EngineVisitorShim(
            agent=agent,
            action_resolver=None,
            user_id=None,
            conversation=conversation,
            interaction=None,
            session_id=None,
            response_bus=None,
            channel=None,
        )
        catalog = await SkillCatalog.discover(
            visitor=shim,
            skills_selector=getattr(action, "skills", None),
            skills_source=getattr(action, "skills_source", "both"),
            denied_skills=list(getattr(action, "denied_skills", [])) or None,
        )
        out: List[str] = []
        for name, sd in catalog.skills.items():
            d = sd.get("dir", "")
            if sd.get("always_active", False) or (
                d and always_active_from_skill_dir(d)
            ):
                out.append(name)
        return out
    except Exception as exc:
        logger.warning("list_always_active_skill_names: %s", exc)
        return []
