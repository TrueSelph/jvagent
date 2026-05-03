"""Hot-reload helpers for AgentInteract (same behavior as SkillInteractAction)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from jvagent.action.skill.skill_catalog import SkillCatalog

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


async def refresh_skills(visitor: "InteractWalker") -> List[str]:
    """Re-discover skills and register any newly installed bundles."""
    state = getattr(visitor, "_skill_state", None)
    if state is None:
        logger.warning("refresh_skills: no _skill_state on visitor")
        return []

    discovered_skills = state.get("discovered_skills") or {}
    skill_catalog = state.get("skill_catalog")
    tool_executor = state.get("tool_executor")
    action = state.get("action")

    await SkillCatalog.invalidate_cache(
        namespace=visitor._agent.namespace,
        agent_name=visitor._agent.name,
    )
    new_catalog = await SkillCatalog.discover(
        visitor=visitor,
        skills_selector=action.skills if action else None,
        skills_source=action.skills_source if action else "both",
        denied_skills=getattr(action, "denied_skills", None) if action else None,
    )
    new_skills = new_catalog.skills
    newly_found = [name for name in new_skills if name not in discovered_skills]

    if not newly_found and new_catalog.skills.keys() == discovered_skills.keys():
        return []

    if tool_executor:
        for skill_name in newly_found:
            skill_data = new_skills[skill_name]
            tool_executor.register_skill_bundle(
                skill_name=skill_name,
                dir_path=skill_data["dir"],
                tool_files=skill_data.get("tool_files", []),
                allowed_tools=skill_data.get("allowed_tools", []),
            )

    discovered_skills.update(new_skills)
    if skill_catalog is not None:
        skill_catalog.skills = discovered_skills

    logger.info(
        "refresh_skills: registered %d new skill(s): %s",
        len(newly_found),
        newly_found,
    )
    return newly_found


async def remove_skill(visitor: "InteractWalker", skill_name: str) -> bool:
    """Hot-unload a skill from the current session."""
    state = getattr(visitor, "_skill_state", None)
    if state is None:
        return False
    discovered_skills = state.get("discovered_skills") or {}
    skill_catalog = state.get("skill_catalog")
    tool_executor = state.get("tool_executor")

    if skill_name not in discovered_skills:
        return False

    if tool_executor:
        tool_executor.unregister_skill_bundle(skill_name)

    discovered_skills.pop(skill_name, None)
    if skill_catalog and isinstance(getattr(skill_catalog, "skills", None), dict):
        skill_catalog.skills.pop(skill_name, None)

    await SkillCatalog.invalidate_cache(
        namespace=visitor._agent.namespace,
        agent_name=visitor._agent.name,
    )
    logger.info("remove_skill: removed skill '%s' from session", skill_name)
    return True
