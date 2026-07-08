"""Validate leadgen skill frontmatter against custom_tools.py."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from jvagent.action.skill_spec.contract import (
    check_skill_md,
    validate_functions_in_custom_tools,
)

from .spec import load_leadgen_spec_from_skill


def validate_leadgen_skill_dir(skill_dir: Path) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    skill_dir = Path(skill_dir)
    try:
        spec = load_leadgen_spec_from_skill(skill_dir)
    except Exception as exc:
        return False, [str(exc)]

    missing_skill_md = check_skill_md(skill_dir)
    if missing_skill_md:
        return False, [missing_skill_md]

    if spec is None:
        return False, ["No leadgen: block in SKILL.md frontmatter"]

    if not spec.fields:
        issues.append("leadgen.fields is empty")

    declared_hooks: List[str] = []
    h = spec.handlers
    for name in (h.post_capture, h.qualify, h.on_sync):
        if name:
            declared_hooks.append(name)
    for t in spec.skill_tools:
        if t.function:
            declared_hooks.append(t.function)

    if declared_hooks:
        issues.extend(validate_functions_in_custom_tools(skill_dir, declared_hooks))

    return len(issues) == 0, issues
