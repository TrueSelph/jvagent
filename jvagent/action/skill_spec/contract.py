"""Shared custom_tools.py contract validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set, Tuple

from .base import SKILL_MD, SkillToolDef, collect_skill_tool_function_refs


def check_skill_md(skill_dir: Path) -> Optional[str]:
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / SKILL_MD
    if not skill_md.is_file():
        return f"Missing SKILL.md at {skill_dir}"
    return None


def check_custom_tools_module(skill_dir: Path) -> Optional[str]:
    module_path = skill_dir / "scripts" / "custom_tools.py"
    if not module_path.is_file():
        return f"Missing scripts/custom_tools.py under {skill_dir}"
    return None


def validate_functions_in_custom_tools(
    skill_dir: Path,
    function_names: Iterable[str],
    *,
    builtins: Optional[Set[str]] = None,
    resolver: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """Return issues for functions not found in ``scripts/custom_tools.py``.

    When ``resolver`` is provided it is called for each non-builtin name and
    should return ``True`` when the function is available. Otherwise a simple
    ``def {name}`` text search is used against ``custom_tools.py``.
    """
    issues: List[str] = []
    names = [n for n in function_names if n]
    if not names:
        return issues

    module_path = skill_dir / "scripts" / "custom_tools.py"
    text: Optional[str] = None
    if resolver is None:
        if not module_path.is_file():
            return [f"Missing scripts/custom_tools.py for hooks: {sorted(set(names))}"]
        text = module_path.read_text(encoding="utf-8")

    builtin_set = builtins or set()
    for name in sorted(set(names)):
        if name in builtin_set:
            continue
        if resolver is not None:
            if not resolver(name):
                issues.append(
                    f"Function '{name}' referenced in frontmatter but not in custom_tools.py"
                )
            continue
        assert text is not None
        if f"def {name}" not in text:
            issues.append(f"Hook function '{name}' not found in custom_tools.py")
    return issues


def validate_folder_name_matches_spec(
    skill_dir: Path,
    spec_name: str,
) -> Optional[str]:
    if spec_name and skill_dir.name != spec_name:
        return (
            f"Folder name '{skill_dir.name}' does not match frontmatter name "
            f"'{spec_name}'"
        )
    return None


def validate_skill_tool_functions(
    skill_dir: Path,
    skill_tools: Iterable[SkillToolDef],
    *,
    resolver: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    return validate_functions_in_custom_tools(
        skill_dir,
        collect_skill_tool_function_refs(list(skill_tools)),
        resolver=resolver,
    )


def validate_skill_package(
    skill_dir: Path,
    *,
    block_label: str,
    spec: Optional[object],
    function_names: Iterable[str],
    builtins: Optional[Set[str]] = None,
    resolver: Optional[Callable[[str], bool]] = None,
    require_custom_tools_when_hooks: bool = True,
) -> Tuple[bool, List[str]]:
    """Shared contract validation skeleton for skill packages."""
    issues: List[str] = []
    skill_dir = skill_dir.resolve()

    missing_skill_md = check_skill_md(skill_dir)
    if missing_skill_md:
        return False, [missing_skill_md]

    if spec is None:
        return False, [f"No `{block_label}:` block in {skill_dir / SKILL_MD}"]

    names = [n for n in function_names if n]
    if names and require_custom_tools_when_hooks:
        missing_module = check_custom_tools_module(skill_dir)
        if missing_module:
            issues.append(missing_module)

    issues.extend(
        validate_functions_in_custom_tools(
            skill_dir,
            names,
            builtins=builtins,
            resolver=resolver,
        )
    )

    spec_name = getattr(spec, "name", "") or ""
    folder_issue = validate_folder_name_matches_spec(skill_dir, spec_name)
    if folder_issue:
        issues.append(folder_issue)

    return len(issues) == 0, issues
