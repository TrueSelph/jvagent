"""Skill hub action.

Exposes jvagent-internal skill-management capabilities as first-class tools
(ADR-0012: actions are first-class tools): search the skills.sh ecosystem,
install a skill bundle from a GitHub source, list installed skills, and
remove an installed skill bundle.

These are jvagent-internal/admin operations (they read and mutate the app's
``agents/<ns>/<agent>/skills/`` directory and ``agent.yaml``), not user-sandbox
code. Installation and removal of bundles that ship executable ``.py`` tool
files are gated behind explicit user confirmation.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jvagent.action.base import Action
from jvagent.action.skill_hub._installer import (
    install_from_download,
    remove_skill_from_yaml,
    update_agent_yaml,
)
from jvagent.action.skill_hub._skills_cli import (
    run_skills_add,
    run_skills_find,
    run_skills_list,
)
from jvagent.core.app_context import get_app_root

logger = logging.getLogger(__name__)


def _resolve_agent_identity(visitor: Any) -> Tuple[str, str]:
    """Extract namespace and agent_name from the visitor's agent."""
    agent = getattr(visitor, "_agent", None)
    if agent is None:
        return "", ""

    namespace = getattr(agent, "namespace", "") or ""
    agent_name = getattr(agent, "name", "") or getattr(agent, "agent_name", "") or ""

    return namespace, agent_name


class SkillHubAction(Action):
    """Search, install, list, and remove agent skill bundles."""

    # -- search_registry ----------------------------------------------------

    async def search_registry(
        self, arguments: Dict[str, Any], *, visitor: Any = None
    ) -> Any:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}

        top_k = arguments.get("top_k", 5)
        try:
            top_k = max(1, min(20, int(top_k)))
        except (TypeError, ValueError):
            top_k = 5

        result = await run_skills_find(query, top_k=top_k)

        if "error" in result:
            return result

        skills = result.get("skills", [])
        if not skills:
            return f"No skills found matching '{query}'."

        # Format as a readable summary for the LLM
        lines = []
        for skill in skills:
            name = skill.get("name", "unknown")
            source = skill.get("source", "unknown")
            installs = skill.get("install_count", "?")
            url = skill.get("url", "")
            line = f"{source}@{name} ({installs} installs)"
            if url:
                line += f"\n  {url}"
            lines.append(line)

        header = f"Found {len(skills)} skill(s) matching '{query}':\n\n"
        return header + "\n\n".join(lines)

    # -- install_skill ------------------------------------------------------

    async def install_skill(
        self, arguments: Dict[str, Any], *, visitor: Any = None
    ) -> Any:
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
                        f"Skill '{skill_name}' contains executable code: "
                        f"[{file_list}]. "
                        "You must ask the user for explicit confirmation before "
                        "installing. Then call install_skill with confirmed=True."
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

    # -- list_installed -----------------------------------------------------

    async def list_installed(
        self, arguments: Dict[str, Any], *, visitor: Any = None
    ) -> Any:
        from jvagent.scaffold.skill_resolve import resolve_merged_skill_bundles

        app_root = get_app_root()
        namespace, agent_name = _resolve_agent_identity(visitor)
        if not namespace or not agent_name:
            return {"error": "Cannot determine agent identity."}

        try:
            bundles = resolve_merged_skill_bundles(
                app_root, namespace, agent_name, include_builtin=True
            )
        except Exception as exc:
            return {"error": f"Failed to resolve installed skills: {exc}"}

        if not bundles:
            return "No skills are currently installed for this agent."

        results = []
        for name, data in sorted(bundles.items()):
            tool_files = data.get("tool_files", [])
            results.append(
                {
                    "name": name,
                    "description": data.get("description", ""),
                    "source": data.get("dir", ""),
                    "has_tools": len(tool_files) > 0,
                    "tool_count": len(tool_files),
                }
            )

        # Format as readable output
        lines = [f"Installed skills ({len(results)}):"]
        for skill in results:
            name = skill["name"]
            desc = skill["description"]
            tools = "with tools" if skill["has_tools"] else "SOP only"
            lines.append(f"  {name} — {desc[:80]} ({tools})")

        return "\n".join(lines)

    # -- remove_skill -------------------------------------------------------

    async def remove_skill(
        self, arguments: Dict[str, Any], *, visitor: Any = None
    ) -> Any:
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

        # Hot-unload from a running session is no longer supported (the legacy
        # in-session skill catalog was removed). The skill is gone from disk and
        # agent.yaml; a session restart clears it from memory.
        hot_unloaded = False

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

    # -- Tool surface -------------------------------------------------------

    async def get_tools(self) -> List[Any]:
        """Expose skill-management capabilities as first-class tools."""
        import json

        from jvagent.tooling.tool import Tool
        from jvagent.tooling.tool_executor import get_dispatch_visitor

        action = self

        async def _search_registry(query: str, top_k: int = 5) -> str:
            arguments: Dict[str, Any] = {"query": query, "top_k": top_k}
            visitor = get_dispatch_visitor()
            result = await action.search_registry(arguments, visitor=visitor)
            return result if isinstance(result, str) else json.dumps(result)

        async def _install_skill(source: str, skill: str, confirmed: bool) -> str:
            arguments: Dict[str, Any] = {
                "source": source,
                "skill": skill,
                "confirmed": confirmed,
            }
            visitor = get_dispatch_visitor()
            result = await action.install_skill(arguments, visitor=visitor)
            return result if isinstance(result, str) else json.dumps(result)

        async def _list_installed() -> str:
            arguments: Dict[str, Any] = {}
            visitor = get_dispatch_visitor()
            result = await action.list_installed(arguments, visitor=visitor)
            return result if isinstance(result, str) else json.dumps(result)

        async def _remove_skill(skill_name: str, confirmed: bool) -> str:
            arguments: Dict[str, Any] = {
                "skill_name": skill_name,
                "confirmed": confirmed,
            }
            visitor = get_dispatch_visitor()
            result = await action.remove_skill(arguments, visitor=visitor)
            return result if isinstance(result, str) else json.dumps(result)

        return [
            Tool(
                name="skill_hub__search_registry",
                description=(
                    "Search the skills.sh ecosystem for available skill bundles. "
                    "Returns matching skills with name, source, and install count. "
                    "Use this when the user asks for new capabilities or says "
                    '"find a skill for X".'
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Search query — keywords, capability name, or "
                                "domain (e.g. 'react testing', 'deployment', "
                                "'calendar')"
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": (
                                "Maximum number of results to return "
                                "(default: 5, max: 20)"
                            ),
                        },
                    },
                    "required": ["query"],
                },
                execute=_search_registry,
            ),
            Tool(
                name="skill_hub__install_skill",
                description=(
                    "Download and install a skill from the skills.sh ecosystem. "
                    "The source is a GitHub repo (owner/repo) and the skill is a "
                    "specific skill name within that repo. "
                    "If the skill contains executable code (.py tool files), you "
                    "MUST set confirmed=True only after the user has explicitly "
                    "approved. For SOP-only skills (no .py files), confirmed may "
                    "be True without prior approval."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": (
                                "GitHub source for the skill (e.g. "
                                "'vercel-labs/agent-skills' or a full GitHub URL)"
                            ),
                        },
                        "skill": {
                            "type": "string",
                            "description": (
                                "Name of the skill to install from the source"
                            ),
                        },
                        "confirmed": {
                            "type": "boolean",
                            "description": (
                                "Whether the user has confirmed installation. Must "
                                "be True when the skill has .py tool files. Set True "
                                "only after explicit user approval."
                            ),
                        },
                    },
                    "required": ["source", "skill", "confirmed"],
                },
                execute=_install_skill,
            ),
            Tool(
                name="skill_hub__list_installed",
                description=(
                    "List skill bundles currently installed for this agent. "
                    "Shows both built-in and app-local skills with their names, "
                    "descriptions, and whether they contain tool modules."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {},
                },
                execute=_list_installed,
            ),
            Tool(
                name="skill_hub__remove_skill",
                description=(
                    "Remove an installed skill bundle from this agent. "
                    "Deletes the skill directory, updates agent.yaml, and "
                    "hot-unloads the skill from the current session. "
                    "Cannot remove built-in skills. "
                    "Requires confirmed=True — you must ask the user for "
                    "explicit confirmation before removing a skill."
                ),
                parameters_schema={
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
                execute=_remove_skill,
            ),
        ]
