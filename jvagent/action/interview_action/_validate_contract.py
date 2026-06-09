"""Validate interview skill frontmatter against scripts/custom_tools.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Set, Tuple

from .core.interview_loader import (
    INTERVIEW_FRONTMATTER_KEY,
    load_interview_spec_from_skill,
)
from .core.validators import BUILTIN_VALIDATORS
from .runtime.hooks import load_hook_function


def _collect_function_refs(spec: Any) -> Set[str]:
    refs: Set[str] = set()
    for f in spec.fields:
        if f.validator:
            refs.add(f.validator)
        if f.input_handler:
            refs.add(f.input_handler)
        refs.update(f.pre_processor or [])
        refs.update(f.post_processor or [])
    for tool in spec.skill_tools or []:
        if tool.function:
            refs.add(tool.function)
        elif tool.name:
            refs.add(tool.name)
    h = spec.handlers
    for fn in (h.review, h.complete, h.reset, h.cancel):
        if fn:
            refs.add(fn)
    return refs


def validate_interview_skill_dir(skill_dir: Path) -> Tuple[bool, List[str]]:
    """Return (ok, issues) for an interview skill package directory."""
    issues: List[str] = []
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return False, [f"Missing SKILL.md at {skill_dir}"]

    spec = load_interview_spec_from_skill(skill_dir)
    if spec is None:
        return False, [f"No `{INTERVIEW_FRONTMATTER_KEY}:` block in {skill_md}"]

    custom_tools = skill_dir / "scripts" / "custom_tools.py"
    if not custom_tools.is_file():
        issues.append(f"Missing scripts/custom_tools.py under {skill_dir}")

    refs = _collect_function_refs(spec)
    for name in sorted(refs):
        if name in BUILTIN_VALIDATORS:
            continue
        func = load_hook_function(spec, name)
        if func is None:
            issues.append(
                f"Function '{name}' referenced in frontmatter but not in custom_tools.py"
            )

    if spec.name and skill_dir.name != spec.name:
        issues.append(
            f"Folder name '{skill_dir.name}' does not match frontmatter name '{spec.name}'"
        )

    return len(issues) == 0, issues
