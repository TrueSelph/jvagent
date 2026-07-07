"""Validate leadgen skill frontmatter against custom_tools.py."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from .spec import load_leadgen_spec_from_skill


def validate_leadgen_skill_dir(skill_dir: Path) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    skill_dir = Path(skill_dir)
    try:
        spec = load_leadgen_spec_from_skill(skill_dir)
    except Exception as exc:
        return False, [str(exc)]

    if spec is None:
        return False, ["No leadgen: block in SKILL.md frontmatter"]

    if not spec.fields:
        issues.append("leadgen.fields is empty")

    module_path = skill_dir / "scripts" / "custom_tools.py"
    declared_hooks: List[str] = []
    h = spec.handlers
    for name in (h.post_capture, h.qualify, h.on_sync):
        if name:
            declared_hooks.append(name)
    for t in spec.skill_tools:
        if t.function:
            declared_hooks.append(t.function)

    if declared_hooks and not module_path.is_file():
        issues.append(f"Missing scripts/custom_tools.py for hooks: {declared_hooks}")
    elif module_path.is_file():
        text = module_path.read_text(encoding="utf-8")
        for hook in declared_hooks:
            if f"def {hook}" not in text:
                issues.append(f"Hook function '{hook}' not found in custom_tools.py")

    return len(issues) == 0, issues
