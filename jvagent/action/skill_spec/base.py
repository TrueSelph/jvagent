"""Shared skill-spec primitives — dataclasses and YAML parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union

SKILL_MD = "SKILL.md"

_SKILL_TOOL_KEYS = frozenset({"name", "description", "function", "parameters"})

T = TypeVar("T")


@dataclass
class SkillToolDef:
    name: str
    description: str = ""
    function: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


def reject_unknown_keys(
    data: Dict[str, Any], allowed: frozenset[str], *, path: str
) -> None:
    for key in data:
        if key not in allowed:
            raise ValueError(f"Unknown frontmatter key '{key}' at {path}")


def parse_string_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return []


def parse_skill_tools(
    raw: Any,
    *,
    path_prefix: str,
    require_mapping: bool = True,
) -> List[SkillToolDef]:
    """Parse ``skill_tools`` entries from a frontmatter block."""
    skill_tools: List[SkillToolDef] = []
    for i, entry in enumerate(raw or []):
        if not entry:
            continue
        if not isinstance(entry, dict):
            if require_mapping:
                raise ValueError(f"skill_tools[{i}] must be a mapping")
            continue
        path = f"{path_prefix}.skill_tools[{i}]"
        reject_unknown_keys(entry, _SKILL_TOOL_KEYS, path=path)
        skill_tools.append(
            SkillToolDef(
                name=str(entry.get("name", "") or ""),
                description=str(entry.get("description", "") or ""),
                function=str(entry.get("function", "") or ""),
                parameters=dict(entry.get("parameters") or {}),
            )
        )
    return skill_tools


def collect_skill_tool_function_refs(
    skill_tools: List[SkillToolDef],
) -> List[str]:
    refs: List[str] = []
    for tool in skill_tools:
        if tool.function:
            refs.append(tool.function)
        elif tool.name:
            refs.append(tool.name)
    return refs


def parse_handlers_mapping(
    data: Any,
    *,
    allowed_keys: frozenset[str],
    path: str,
    builder: Callable[[Dict[str, Any]], T],
    string_fields: Optional[frozenset[str]] = None,
) -> T:
    """Shared handler-block parsing pattern for domain ``HandlersDef`` types."""
    if not data:
        return builder({})
    if not isinstance(data, dict):
        raise ValueError("handlers must be a mapping of handler name to function name")
    reject_unknown_keys(data, allowed_keys, path=path)
    if string_fields:
        for key in string_fields:
            val = data.get(key)
            if val is not None and not isinstance(val, str):
                raise ValueError(f"handlers.{key} must be a function name string")
    return builder(data)


def load_frontmatter_block_from_skill(
    skill_dir: Union[str, Path],
    *,
    block_key: str,
) -> Tuple[Optional[Dict[str, Any]], str, Path]:
    """Load a named frontmatter block from ``SKILL.md``.

    Returns ``(block_data, default_name, skill_file)``. ``block_data`` is ``None``
    when the skill file is missing or the block is absent.
    """
    skill_dir = Path(skill_dir)
    skill_file = skill_dir / SKILL_MD
    if not skill_file.is_file():
        return None, skill_dir.name, skill_file

    from jvagent.scaffold.skill_resolve import _parse_frontmatter

    raw = skill_file.read_text(encoding="utf-8")
    frontmatter, _content = _parse_frontmatter(raw, skill_file)
    default_name = str(frontmatter.get("name") or skill_dir.name).strip()
    block_data = frontmatter.get(block_key)
    if not block_data:
        return None, default_name, skill_file
    if not isinstance(block_data, dict):
        raise ValueError(
            f"Frontmatter '{block_key}' must be a mapping in {skill_file}"
        )
    return block_data, default_name, skill_file
