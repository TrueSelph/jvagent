"""jvagent-specific skill installation logic.

Handles copying downloaded skill files from a temp ``npx skills add``
output directory into jvagent's agent-local ``skills/`` directory,
and updating ``agent.yaml`` to include the newly installed skill.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = frozenset({".py", ".md"})
_ALLOWED_BASENAMES = frozenset({"__init__.py"})
_SKILL_INTERACT_ACTION_NAME = "jvagent/skill_interact_action"


def install_from_download(
    skill_name: str,
    download_dir: Path,
    target_dir: Path,
) -> List[str]:
    """Copy downloaded skill files into the jvagent skills directory.

    Scans ``download_dir`` recursively for files belonging to the skill,
    validates them, and copies only safe files (.py, .md, __init__.py).

    Args:
        skill_name: The skill bundle name (used as the subdirectory).
        download_dir: Directory where ``npx skills add`` placed files
            (e.g. a ``.claude/skills/<skill>/`` directory).
        target_dir: The jvagent agent skills directory
            (e.g. ``<app_root>/agents/<ns>/<agent>/skills/``).

    Returns:
        List of relative file paths that were copied.

    Raises:
        ValueError: If a path traversal or disallowed file is detected,
            or if no SKILL.md is found.
    """
    dest = target_dir / skill_name
    copied: List[str] = []

    if not download_dir.is_dir():
        raise ValueError(f"Download directory does not exist: {download_dir}")

    # Collect candidate files
    source_files = _collect_safe_files(download_dir, skill_name)
    if not source_files:
        raise ValueError(f"No valid skill files found in {download_dir}")

    # Validate that SKILL.md exists
    has_skill_md = any(f.name == "SKILL.md" for f in source_files)
    if not has_skill_md:
        raise ValueError(f"SKILL.md not found in download for skill '{skill_name}'")

    # Copy files
    dest.mkdir(parents=True, exist_ok=True)
    for src_file in source_files:
        # Compute relative path from the skill subdirectory
        try:
            rel = src_file.relative_to(download_dir)
        except ValueError:
            # File might be in a nested skill directory
            rel = Path(src_file.name)

        dst_file = dest / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        # Final path safety check
        _validate_path_safety(dst_file, dest)

        shutil.copy2(src_file, dst_file)
        copied.append(str(rel))

    return copied


def _collect_safe_files(download_dir: Path, skill_name: str) -> List[Path]:
    """Collect files from the download directory that are safe to copy.

    Looks for files in two locations:
    1. Directly under ``download_dir`` (if it's the skill directory itself)
    2. Under ``download_dir/<skill_name>/`` (if npx placed it in a subdirectory)
    """
    candidates: List[Path] = []

    # Check if files are directly in download_dir or in a skill_name subdirectory
    search_dirs = [download_dir]
    skill_subdir = download_dir / skill_name
    if skill_subdir.is_dir():
        search_dirs.append(skill_subdir)

    for search_dir in search_dirs:
        for item in search_dir.rglob("*"):
            if not item.is_file():
                continue
            if not _is_allowed_file(item):
                logger.debug("Skipping disallowed file: %s", item)
                continue
            candidates.append(item)

    # Deduplicate (in case both search paths find the same files)
    seen = set()
    unique: List[Path] = []
    for f in candidates:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


def _is_allowed_file(path: Path) -> bool:
    """Check if a file is safe to include in a skill bundle."""
    name = path.name
    if name in _ALLOWED_BASENAMES:
        return True
    return path.suffix in _ALLOWED_EXTENSIONS


def _validate_path_safety(file_path: Path, base_dir: Path) -> None:
    """Ensure a file path does not escape the target directory."""
    try:
        file_path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        raise ValueError(f"Path traversal detected: {file_path} escapes {base_dir}")


def update_agent_yaml(
    app_root: str,
    namespace: str,
    agent_name: str,
    skill_name: str,
) -> bool:
    """Add a skill name to the SkillInteractAction's skills list in agent.yaml.

    Args:
        app_root: The jvagent app root directory.
        namespace: The agent's namespace.
        agent_name: The agent's name.
        skill_name: The skill to add.

    Returns:
        True if agent.yaml was updated, False if no change was needed.

    Raises:
        FileNotFoundError: If agent.yaml does not exist.
        ValueError: If agent.yaml cannot be parsed.
    """
    agent_yaml_path = Path(app_root) / "agents" / namespace / agent_name / "agent.yaml"
    if not agent_yaml_path.is_file():
        raise FileNotFoundError(f"agent.yaml not found at {agent_yaml_path}")

    data = _read_yaml(agent_yaml_path)
    actions = data.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("agent.yaml 'actions' must be a list")

    # Find the SkillInteractAction entry
    action_entry = _find_skill_action(actions)
    if action_entry is None:
        logger.warning(
            "No SkillInteractAction entry found in agent.yaml; "
            "skill '%s' installed but not auto-configured",
            skill_name,
        )
        return False

    context = action_entry.get("context", {})
    if not isinstance(context, dict):
        context = {}
        action_entry["context"] = context

    skills_val = context.get("skills")

    # If skills is "-all", no change needed
    if isinstance(skills_val, str) and skills_val.strip() == "-all":
        return False

    needs_write = False

    # If skills is a list, append if not present
    if isinstance(skills_val, list):
        if skill_name not in skills_val:
            skills_val.append(skill_name)
            needs_write = True
    else:
        # If skills is missing/None, initialize with skill_hub + new skill
        context["skills"] = ["skill_hub", skill_name]
        needs_write = True

    if needs_write:
        _write_yaml(agent_yaml_path, data)
    return True


def remove_skill_from_yaml(
    app_root: str,
    namespace: str,
    agent_name: str,
    skill_name: str,
) -> bool:
    """Remove a skill name from the SkillInteractAction's skills list in agent.yaml.

    Args:
        app_root: The jvagent app root directory.
        namespace: The agent's namespace.
        agent_name: The agent's name.
        skill_name: The skill to remove.

    Returns:
        True if agent.yaml was updated, False if no change was needed.

    Raises:
        FileNotFoundError: If agent.yaml does not exist.
        ValueError: If agent.yaml cannot be parsed.
    """
    agent_yaml_path = Path(app_root) / "agents" / namespace / agent_name / "agent.yaml"
    if not agent_yaml_path.is_file():
        raise FileNotFoundError(f"agent.yaml not found at {agent_yaml_path}")

    data = _read_yaml(agent_yaml_path)
    actions = data.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("agent.yaml 'actions' must be a list")

    action_entry = _find_skill_action(actions)
    if action_entry is None:
        return False

    context = action_entry.get("context", {})
    if not isinstance(context, dict):
        return False

    skills_val = context.get("skills")
    needs_write = False

    # If skills is "-all", add to denied_skills instead
    if isinstance(skills_val, str) and skills_val.strip() == "-all":
        denied = context.get("denied_skills", [])
        if not isinstance(denied, list):
            denied = []
        if skill_name not in denied:
            denied.append(skill_name)
            context["denied_skills"] = denied
            needs_write = True
    elif isinstance(skills_val, list):
        if skill_name in skills_val:
            skills_val.remove(skill_name)
            needs_write = True

    if needs_write:
        _write_yaml(agent_yaml_path, data)
    return needs_write


def _find_skill_action(actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the SkillInteractAction entry in the actions list."""
    for entry in actions:
        action_name = entry.get("action", "")
        if action_name == _SKILL_INTERACT_ACTION_NAME:
            return entry
    return None


def _read_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML in {path}")
    return data


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Write a YAML file, preserving key order."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
