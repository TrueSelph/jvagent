"""Validate interview skill frontmatter against scripts/custom_tools.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Set, Tuple

from jvagent.action.skill_spec.contract import (
    check_custom_tools_module,
    validate_skill_package,
)

from .hooks import BUILTIN_HOOKS, load_hook_function
from .spec import INTERVIEW_FRONTMATTER_KEY, load_interview_spec_from_skill
from .validators import BUILTIN_VALIDATORS


def _collect_function_refs(spec: Any) -> Set[str]:
    refs: Set[str] = set()
    for f in spec.fields:
        if f.validator:
            refs.add(f.validator)
        refs.update(f.pre_processor or [])
        refs.update(f.post_processor or [])
        if f.for_each:
            for child in f.for_each.fields:
                if child.validator:
                    refs.add(child.validator)
                refs.update(child.pre_processor or [])
                refs.update(child.post_processor or [])
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
    skill_dir = skill_dir.resolve()
    spec = load_interview_spec_from_skill(skill_dir)

    def _resolver(name: str) -> bool:
        return load_hook_function(spec, name) is not None

    ok, issues = validate_skill_package(
        skill_dir,
        block_label=INTERVIEW_FRONTMATTER_KEY,
        spec=spec,
        function_names=sorted(_collect_function_refs(spec)) if spec else [],
        builtins=set(BUILTIN_VALIDATORS) | set(BUILTIN_HOOKS),
        resolver=_resolver if spec else None,
        require_custom_tools_when_hooks=False,
    )
    if spec is not None:
        missing_module = check_custom_tools_module(skill_dir)
        if missing_module:
            issues.append(missing_module)
            ok = False
    return ok, issues
