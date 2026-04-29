"""Tool: remove_skill — uninstall a skill bundle from this agent."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict

from jvagent.core.app_context import get_app_root
from jvagent.skills.skill_hub._installer import remove_skill_from_yaml

logger = logging.getLogger(__name__)


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "remove_skill",
        "description": (
            "Remove an installed skill bundle from this agent. "
            "Deletes the skill directory, updates agent.yaml, and "
            "hot-unloads the skill from the current session. "
            "Cannot remove built-in skills. "
            "Requires confirmed=True — you must ask the user for "
            "explicit confirmation before removing a skill."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the installed skill to remove",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Whether the user has confirmed removal. "
                        "Must be True before removal proceeds."
                    ),
                },
            },
            "required": ["skill_name", "confirmed"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any = None) -> Any:
    skill_name = str(arguments.get("skill_name", "")).strip()
    confirmed = bool(arguments.get("confirmed", False))

    if not skill_name:
        return {"error": "'skill_name' is required."}

    if not confirmed:
        return {
            "error": (
                f"Removal of '{skill_name}' requires user confirmation. "
                "Present the skill details to the user and ask for explicit "
                "confirmation, then call remove_skill with confirmed=True."
            )
        }

    app_root = get_app_root()
    namespace, agent_name = _resolve_agent_identity(visitor)
    if not namespace or not agent_name:
        return {"error": "Cannot determine agent identity."}

    skills_dir = Path(app_root) / "agents" / namespace / agent_name / "skills"
    target_dir = skills_dir / skill_name

    # Verify the skill exists on disk
    if not target_dir.is_dir() or not (target_dir / "SKILL.md").is_file():
        return {"error": f"Skill '{skill_name}' is not installed locally."}

    # Reject built-in skills by checking if the directory is inside jvagent/skills
    from jvagent.scaffold.skill_resolve import _resolve_builtin_root

    builtin_root = _resolve_builtin_root()
    if builtin_root and target_dir.resolve().is_relative_to(builtin_root.resolve()):
        return {
            "error": (
                f"Skill '{skill_name}' is a built-in skill and cannot be removed."
            )
        }

    # Delete skill directory
    try:
        shutil.rmtree(target_dir)
    except Exception as exc:
        return {"error": f"Failed to delete skill directory: {exc}"}

    # Update agent.yaml
    yaml_updated = False
    try:
        yaml_updated = remove_skill_from_yaml(
            app_root, namespace, agent_name, skill_name
        )
    except Exception as exc:
        logger.warning("Failed to update agent.yaml: %s", exc, exc_info=True)

    # Hot-unload from current session
    hot_unloaded = False
    try:
        from jvagent.action.skill.skill_interact_action import SkillInteractAction

        hot_unloaded = await SkillInteractAction.remove_skill(visitor, skill_name)
    except Exception as exc:
        logger.warning(
            "Hot-unload failed (skill removed from disk but still in session): %s",
            exc,
            exc_info=True,
        )

    msg_parts = [f"Skill '{skill_name}' removed successfully."]
    if hot_unloaded:
        msg_parts.append("It has been unloaded from the current session.")
    else:
        msg_parts.append("Restart the session to fully clear it from memory.")
    if not yaml_updated:
        msg_parts.append("Update agent.yaml manually if needed.")

    return {
        "status": "removed",
        "skill_name": skill_name,
        "yaml_updated": yaml_updated,
        "hot_unloaded": hot_unloaded,
        "message": " ".join(msg_parts),
    }


def _resolve_agent_identity(visitor: Any) -> tuple:
    """Extract namespace and agent_name from the visitor's agent."""
    agent = getattr(visitor, "_agent", None)
    if agent is None:
        return "", ""

    namespace = getattr(agent, "namespace", "") or ""
    agent_name = getattr(agent, "name", "") or getattr(agent, "agent_name", "") or ""

    return namespace, agent_name
