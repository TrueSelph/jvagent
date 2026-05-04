"""AgentInteract-only extensions to skill run context (keeps ``action/skill`` generic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from jvagent.action.skill.skill_action_contracts import SkillRunContext


@dataclass
class AgentInteractSkillRunContext(SkillRunContext):
    """Skill run context for AgentInteract with router-selected preload names."""

    preloaded_skills: List[str] = field(default_factory=list)
