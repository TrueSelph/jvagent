"""Interview spec — frontmatter schema parsing, dataclasses, and registry.

Canonical source: ``interview:`` block in ``SKILL.md`` frontmatter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

logger = logging.getLogger(__name__)

SKILL_MD = "SKILL.md"
INTERVIEW_FRONTMATTER_KEY = "interview"

ConfirmMode = Literal["manual", "auto"]

_INTERVIEW_KEYS = frozenset(
    {
        "name",
        "title",
        "summary",
        "confirm",
        "fields",
        "handlers",
        "skill_tools",
    }
)

_FIELD_KEYS = frozenset(
    {
        "key",
        "prompt",
        "guidance",
        "required",
        "validator",
        "validator_args",
        "pre_processor",
        "post_processor",
        "branches",
        "else",
    }
)

_BRANCH_KEYS = frozenset({"when", "goto"})

_HANDLER_KEYS = frozenset({"review", "complete", "reset", "cancel"})

_SKILL_TOOL_KEYS = frozenset({"name", "description", "function", "parameters"})


@dataclass
class BranchDef:
    when: Dict[str, Any] = field(default_factory=dict)
    goto: str = ""


@dataclass
class FieldDef:
    key: str
    prompt: str
    guidance: str = ""
    required: bool = True
    validator: str = ""
    validator_args: Dict[str, Any] = field(default_factory=dict)
    pre_processor: List[str] = field(default_factory=list)
    post_processor: List[str] = field(default_factory=list)
    branches: List[BranchDef] = field(default_factory=list)
    else_field: Optional[str] = None


@dataclass
class HandlersDef:
    review: Optional[str] = None
    complete: Optional[str] = None
    reset: Optional[str] = None
    cancel: Optional[str] = None


@dataclass
class SkillToolDef:
    name: str
    description: str = ""
    function: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InterviewSpec:
    name: str
    title: str = ""
    summary: str = ""
    fields: List[FieldDef] = field(default_factory=list)
    skill_tools: List[SkillToolDef] = field(default_factory=list)
    handlers: HandlersDef = field(default_factory=HandlersDef)
    confirm: ConfirmMode = "manual"
    source_dir: str = ""

    def get_required_fields(self) -> List[str]:
        return [f.key for f in self.fields if f.required]

    def get_field(self, key: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def get_skill_tool(self, name: str) -> Optional[SkillToolDef]:
        for t in self.skill_tools:
            if t.name == name:
                return t
        return None

    def field_keys(self) -> List[str]:
        return [f.key for f in self.fields]


def field_def_to_dict(f: FieldDef) -> Dict[str, Any]:
    """Serialize a FieldDef for tool responses (field_definitions)."""
    result: Dict[str, Any] = {
        "key": f.key,
        "prompt": f.prompt,
        "guidance": f.guidance,
        "required": f.required,
        "validator": f.validator,
    }
    if f.validator_args:
        result["validator_args"] = f.validator_args
    if f.pre_processor:
        result["pre_processor"] = f.pre_processor
    if f.post_processor:
        result["post_processor"] = f.post_processor
    if f.branches:
        result["branches"] = [{"when": b.when, "goto": b.goto} for b in f.branches]
    if f.else_field:
        result["else"] = f.else_field
    return result


def _reject_unknown_keys(
    data: Dict[str, Any], allowed: frozenset[str], *, path: str
) -> None:
    for key in data:
        if key not in allowed:
            raise ValueError(f"Unknown frontmatter key '{key}' at {path}")


def _parse_string_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return []


def _parse_branch(data: Dict[str, Any], *, path: str) -> BranchDef:
    if not isinstance(data, dict):
        raise ValueError(f"Branch at {path} must be a mapping")
    _reject_unknown_keys(data, _BRANCH_KEYS, path=path)
    return BranchDef(
        when=data.get("when", {}) or {},
        goto=data.get("goto", "") or "",
    )


def _parse_field(data: Dict[str, Any], *, index: int) -> FieldDef:
    path = f"fields[{index}]"
    if not isinstance(data, dict):
        raise ValueError(f"Field at {path} must be a mapping")
    _reject_unknown_keys(data, _FIELD_KEYS, path=path)
    validator = data.get("validator", "")
    if validator is not None and not isinstance(validator, str):
        raise ValueError(
            f"validator at {path} must be a function name string; "
            "use validator_args for parameters"
        )

    branches = [
        _parse_branch(b, path=f"{path}.branches[{i}]")
        for i, b in enumerate(data.get("branches", []) or [])
    ]
    return FieldDef(
        key=str(data.get("key", "") or "").strip(),
        prompt=str(data.get("prompt", "") or ""),
        guidance=str(data.get("guidance", "") or ""),
        required=bool(data.get("required", True)),
        validator=str(validator or "").strip(),
        validator_args=dict(data.get("validator_args") or {}),
        pre_processor=_parse_string_list(data.get("pre_processor")),
        post_processor=_parse_string_list(data.get("post_processor")),
        branches=branches,
        else_field=data.get("else"),
    )


def _parse_handlers(data: Any) -> HandlersDef:
    if not data:
        return HandlersDef()
    if not isinstance(data, dict):
        raise ValueError("handlers must be a mapping of handler name to function name")
    _reject_unknown_keys(data, _HANDLER_KEYS, path="interview.handlers")
    for key in ("review", "complete", "reset", "cancel"):
        val = data.get(key)
        if val is not None and not isinstance(val, str):
            raise ValueError(f"handlers.{key} must be a function name string")
    return HandlersDef(
        review=data.get("review"),
        complete=data.get("complete"),
        reset=data.get("reset"),
        cancel=data.get("cancel"),
    )


def _parse_confirm(raw: Any) -> ConfirmMode:
    if raw is None or raw == "":
        return "manual"
    mode = str(raw).strip().lower()
    if mode not in ("manual", "auto"):
        raise ValueError("confirm must be 'manual' or 'auto'")
    return mode  # type: ignore[return-value]


def parse_interview_spec(
    data: Dict[str, Any],
    *,
    source_dir: str,
    default_name: str = "",
) -> InterviewSpec:
    """Build ``InterviewSpec`` from a parsed mapping (frontmatter)."""
    if not isinstance(data, dict):
        raise ValueError("Interview spec must be a YAML mapping")

    _reject_unknown_keys(data, _INTERVIEW_KEYS, path="interview")

    name = str(data.get("name") or default_name or "").strip()
    if default_name and data.get("name"):
        declared = str(data["name"]).strip()
        if declared and declared != default_name:
            logger.warning(
                "Interview spec name %r does not match skill name %r in %s",
                declared,
                default_name,
                source_dir,
            )
            name = declared

    fields = [
        _parse_field(q, index=i) for i, q in enumerate(data.get("fields", []) or [])
    ]
    skill_tools: List[SkillToolDef] = []
    for i, t in enumerate(data.get("skill_tools", []) or []):
        if not t:
            continue
        if not isinstance(t, dict):
            raise ValueError(f"skill_tools[{i}] must be a mapping")
        _reject_unknown_keys(t, _SKILL_TOOL_KEYS, path=f"interview.skill_tools[{i}]")
        skill_tools.append(
            SkillToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                function=t.get("function", ""),
                parameters=t.get("parameters", {}) or {},
            )
        )

    return InterviewSpec(
        name=name,
        title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""),
        fields=fields,
        skill_tools=skill_tools,
        handlers=_parse_handlers(data.get("handlers")),
        confirm=_parse_confirm(data.get("confirm")),
        source_dir=source_dir,
    )


def load_interview_spec_from_skill(
    skill_dir: Union[str, Path]
) -> Optional[InterviewSpec]:
    """Load interview spec from ``SKILL.md`` frontmatter ``interview:`` block."""
    skill_dir = Path(skill_dir)
    skill_file = skill_dir / SKILL_MD
    if not skill_file.is_file():
        return None

    from jvagent.scaffold.skill_resolve import _parse_frontmatter

    raw = skill_file.read_text(encoding="utf-8")
    frontmatter, _content = _parse_frontmatter(raw, skill_file)
    interview_data = frontmatter.get(INTERVIEW_FRONTMATTER_KEY)
    if not interview_data:
        return None
    if not isinstance(interview_data, dict):
        raise ValueError(
            f"Frontmatter '{INTERVIEW_FRONTMATTER_KEY}' must be a mapping in {skill_file}"
        )

    default_name = str(frontmatter.get("name") or skill_dir.name).strip()
    return parse_interview_spec(
        interview_data,
        source_dir=str(skill_dir),
        default_name=default_name,
    )


class InterviewRegistry:
    """Discovers, loads, and caches interview specs from skill directories."""

    def __init__(self) -> None:
        self._specs: Dict[str, InterviewSpec] = {}

    def discover(self, skills_dirs: List[str]) -> Dict[str, InterviewSpec]:
        for skills_dir in skills_dirs:
            skills_path = Path(skills_dir)
            if not skills_path.is_dir():
                continue
            for skill_dir in skills_path.iterdir():
                if not skill_dir.is_dir() or not (skill_dir / SKILL_MD).is_file():
                    continue
                try:
                    spec = load_interview_spec_from_skill(skill_dir)
                except Exception as e:
                    logger.error(
                        "Failed to load interview spec from %s: %s", skill_dir, e
                    )
                    continue
                if spec is None or not spec.name:
                    continue
                self._specs[spec.name] = spec
                logger.info("Loaded interview spec: %s from %s", spec.name, skill_dir)
        return self._specs

    def get(self, name: str) -> Optional[InterviewSpec]:
        return self._specs.get(name)

    def list_specs(self) -> List[str]:
        return list(self._specs.keys())

    def reload(self, skills_dirs: List[str]) -> Dict[str, InterviewSpec]:
        self._specs.clear()
        return self.discover(skills_dirs)

    @property
    def specs(self) -> Dict[str, InterviewSpec]:
        return self._specs
