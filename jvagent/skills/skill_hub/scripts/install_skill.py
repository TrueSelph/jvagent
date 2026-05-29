"""Tool: install_skill — download and install a skill bundle from the skills.sh ecosystem."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from jvagent.core.app_context import get_app_root
from jvagent.skills.skill_hub._installer import install_from_download, update_agent_yaml
from jvagent.skills.skill_hub._skills_cli import run_skills_add, run_skills_list

logger = logging.getLogger(__name__)


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "install_skill",
        "description": (
            "Download and install a skill from the skills.sh ecosystem. "
            "The source is a GitHub repo (owner/repo) and the skill is a specific "
            "skill name within that repo. "
            "If the skill contains executable code (.py tool files), you MUST "
            "set confirmed=True only after the user has explicitly approved. "
            "For SOP-only skills (no .py files), confirmed may be True without "
            "prior approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "GitHub source for the skill (e.g. 'vercel-labs/agent-skills' "
                        "or a full GitHub URL)"
                    ),
                },
                "skill": {
                    "type": "string",
                    "description": "Name of the skill to install from the source",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Whether the user has confirmed installation. Must be True "
                        "when the skill has .py tool files. Set True only after "
                        "explicit user approval."
                    ),
                },
            },
            "required": ["source", "skill", "confirmed"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any = None) -> Any:
    source = str(arguments.get("source", "")).strip()
    skill_name = str(arguments.get("skill", "")).strip()
    confirmed = bool(arguments.get("confirmed", False))

    if not source or not skill_name:
        return {"error": "Both 'source' and 'skill' are required."}

    # Resolve agent identity and paths
    app_root = get_app_root()
    namespace, agent_name = _resolve_agent_identity(visitor)
    if not namespace or not agent_name:
        return {
            "error": (
                "Cannot determine agent identity. "
                "Installation requires an active agent session."
            )
        }

    skills_dir = Path(app_root) / "agents" / namespace / agent_name / "skills"
    target_skill_dir = skills_dir / skill_name

    # Check if already installed
    if target_skill_dir.is_dir() and (target_skill_dir / "SKILL.md").is_file():
        return {
            "error": (
                f"Skill '{skill_name}' is already installed locally. "
                "Remove it first if you want to reinstall."
            )
        }

    # Check if skill has .py tool files by listing the repo
    list_result = await run_skills_list(source)
    if "error" in list_result:
        # List failed — proceed cautiously, assume it might have tools
        logger.warning(
            "Could not list skills in %s: %s", source, list_result.get("error")
        )
        has_tools = None
    else:
        repo_skills = list_result.get("skills", [])
        matching = [s for s in repo_skills if s.get("name") == skill_name]
        has_tools = matching[0].get("has_tools") if matching else None

    # Security gate: if skill has .py files and user hasn't confirmed
    if has_tools is True and not confirmed:
        return {
            "error": (
                f"Skill '{skill_name}' contains executable code (.py tool files). "
                "You must present the details to the user and ask for explicit "
                "confirmation before installing. Then call install_skill again "
                "with confirmed=True."
            )
        }

    # If has_tools is unknown, require confirmation as a precaution
    if has_tools is None and not confirmed:
        return {
            "error": (
                f"Cannot determine if '{skill_name}' has executable code. "
                "As a precaution, ask the user for confirmation before "
                "installing, then call install_skill with confirmed=True."
            )
        }

    # Download via npx skills add in a temp directory
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="jvagent_skill_")
        # Create a .claude directory so npx skills has a recognized agent path
        claude_dir = Path(temp_dir) / ".claude" / "skills"
        claude_dir.mkdir(parents=True, exist_ok=True)

        add_result = await run_skills_add(source, skill_name, cwd=temp_dir)
        if "error" in add_result:
            return {"error": f"Download failed: {add_result['error']}"}

        # Find the downloaded skill files (npx installs to .claude/skills/<skill>/)
        download_path = claude_dir / skill_name
        if not download_path.is_dir():
            # Search for it anywhere under .claude/skills/
            for candidate in claude_dir.iterdir():
                if candidate.is_dir() and (candidate / "SKILL.md").is_file():
                    download_path = candidate
                    break

        if not download_path.is_dir() or not (download_path / "SKILL.md").is_file():
            return {
                "error": (
                    f"Downloaded skill files not found in expected location. "
                    "The npx skills add command may have placed files in an "
                    "unexpected directory."
                )
            }

        # Check for .py tool files in the download
        py_files = [
            f
            for f in download_path.iterdir()
            if f.suffix == ".py" and not f.name.startswith("_")
        ]
        actual_has_tools = len(py_files) > 0

        # Final confirmation gate with actual file check
        if actual_has_tools and not confirmed:
            file_list = ", ".join(f.name for f in py_files)
            return {
                "error": (
                    f"Skill '{skill_name}' contains executable code: [{file_list}]. "
                    "You must ask the user for explicit confirmation before installing. "
                    "Then call install_skill with confirmed=True."
                )
            }

        # Copy files to jvagent skills directory
        copied_files = install_from_download(
            skill_name=skill_name,
            download_dir=download_path,
            target_dir=skills_dir,
        )

        # Validate the installed bundle
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        parsed = parse_skill_bundle(target_skill_dir, source="app")
        if parsed is None:
            # Bundle installed but validation failed — still return success
            logger.warning(
                "Installed skill '%s' did not pass parse_skill_bundle validation",
                skill_name,
            )

        # Update agent.yaml
        yaml_updated = False
        try:
            yaml_updated = update_agent_yaml(
                app_root, namespace, agent_name, skill_name
            )
        except Exception as exc:
            logger.warning("Failed to update agent.yaml: %s", exc, exc_info=True)

        # Hot-load into a running session is no longer supported (the legacy
        # in-session skill catalog was removed). The skill is on disk and in
        # agent.yaml; it is picked up on the next interaction / session start.
        hot_loaded: list = []

        # Build message
        msg_parts = [f"Skill '{skill_name}' installed successfully."]
        if hot_loaded:
            msg_parts.append("It is now available in this session.")
        else:
            msg_parts.append("It will be available on the next interaction.")
        if not yaml_updated:
            msg_parts.append("Update agent.yaml manually if needed.")

        return {
            "status": "installed",
            "skill_name": skill_name,
            "install_path": str(target_skill_dir),
            "files": copied_files,
            "has_tools": actual_has_tools,
            "yaml_updated": yaml_updated,
            "hot_loaded": bool(hot_loaded),
            "message": " ".join(msg_parts),
        }

    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("install_skill failed: %s", exc, exc_info=True)
        return {"error": f"Installation failed: {exc}"}
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def _resolve_agent_identity(visitor: Any) -> tuple:
    """Extract namespace and agent_name from the visitor's agent."""
    agent = getattr(visitor, "_agent", None)
    if agent is None:
        return "", ""

    namespace = getattr(agent, "namespace", "") or ""
    agent_name = getattr(agent, "name", "") or getattr(agent, "agent_name", "") or ""

    return namespace, agent_name
