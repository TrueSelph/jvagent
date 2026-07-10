"""Interview spec — frontmatter schema parsing, dataclasses, and registry.

Canonical source: ``interview:`` block in ``SKILL.md`` frontmatter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from jvagent.action.skill_spec.base import (
    SkillToolDef,
    load_frontmatter_block_from_skill,
    parse_handlers_mapping,
    parse_skill_tools,
    parse_string_list,
    reject_unknown_keys,
)
from jvagent.action.skill_spec.registry import BaseSkillRegistry

logger = logging.getLogger(__name__)

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
        "parameters",
    }
)

_PARAMETER_KEYS = frozenset({"scope", "condition", "response"})

_FIELD_KEYS = frozenset(
    {
        "key",
        "prompt",
        "guidance",
        "hint",
        "required",
        "validator",
        "validator_args",
        "pre_processor",
        "post_processor",
        "branches",
        "else",
        "for_each",
        "for_each_prefix",
    }
)

_FOR_EACH_KEYS = frozenset({"fields"})

# Subpart fields inside for_each — no branches/else/nested for_each in v1.
_FOR_EACH_CHILD_FIELD_KEYS = frozenset(
    {
        "key",
        "prompt",
        "guidance",
        "hint",
        "required",
        "validator",
        "validator_args",
        "pre_processor",
        "post_processor",
    }
)

_BRANCH_KEYS = frozenset({"when", "goto"})

_HANDLER_KEYS = frozenset({"review", "complete", "reset", "cancel"})


@dataclass
class BranchDef:
    when: Dict[str, Any] = field(default_factory=dict)
    goto: str = ""


@dataclass
class ForEachDef:
    """Per-item subpart field templates declared under a parent field."""

    fields: List["FieldDef"] = field(default_factory=list)


@dataclass
class FieldDef:
    key: str
    prompt: str
    guidance: str = ""
    # Plain answer-guidance FOR THE USER — how to answer this question (e.g. "enter
    # your first, last, and any other names"; an accepted format; that a field is
    # optional). Woven into the prompt's user-facing text so the agent instructs the
    # user on the intended answer, and surfaced in field_reference / next_field so
    # the model can answer the user's clarifications. Phrase it as what to tell the
    # user, non-redundant with ``prompt``. Distinct from ``guidance``, which is
    # model-facing acceptance criteria for judging the answer.
    hint: str = ""
    required: bool = True
    validator: str = ""
    validator_args: Dict[str, Any] = field(default_factory=dict)
    pre_processor: List[str] = field(default_factory=list)
    post_processor: List[str] = field(default_factory=list)
    branches: List[BranchDef] = field(default_factory=list)
    else_field: Optional[str] = None
    for_each: Optional[ForEachDef] = None
    for_each_prefix: str = ""


@dataclass
class HandlersDef:
    review: Optional[str] = None
    complete: Optional[str] = None
    reset: Optional[str] = None
    cancel: Optional[str] = None


@dataclass
class InterviewSpec:
    name: str
    title: str = ""
    summary: str = ""
    fields: List[FieldDef] = field(default_factory=list)
    skill_tools: List[SkillToolDef] = field(default_factory=list)
    handlers: HandlersDef = field(default_factory=HandlersDef)
    confirm: ConfirmMode = "manual"
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    source_dir: str = ""

    def get_required_fields(self) -> List[str]:
        return [f.key for f in self.fields if f.required]

    def get_field(self, key: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def get_for_each_child_field(
        self, parent_key: str, child_key: str
    ) -> Optional[FieldDef]:
        parent = self.get_field(parent_key)
        if not parent or not parent.for_each:
            return None
        for child in parent.for_each.fields:
            if child.key == child_key:
                return child
        return None

    def all_for_each_child_keys(self) -> frozenset[str]:
        keys: set[str] = set()
        for f in self.fields:
            if f.for_each:
                for child in f.for_each.fields:
                    keys.add(child.key)
        return frozenset(keys)

    def get_skill_tool(self, name: str) -> Optional[SkillToolDef]:
        for t in self.skill_tools:
            if t.name == name:
                return t
        return None

    def field_keys(self) -> List[str]:
        return [f.key for f in self.fields]


def fields_reference(spec: InterviewSpec) -> List[Dict[str, Any]]:
    """Model-facing field catalog: key, prompt, guidance, required only.

    Server internals (validator, pre/post processors, branches) are executed
    programmatically and are deliberately excluded — the model never needs them.
    """
    out: List[Dict[str, Any]] = []
    for f in spec.fields:
        entry: Dict[str, Any] = {
            "key": f.key,
            "prompt": f.prompt,
            "guidance": f.guidance,
            "required": f.required,
        }
        if f.hint:
            entry["hint"] = f.hint
        if f.for_each and f.for_each.fields:
            subparts: List[Dict[str, Any]] = []
            for child in f.for_each.fields:
                sub: Dict[str, Any] = {
                    "key": child.key,
                    "prompt": child.prompt,
                    "required": child.required,
                }
                if child.guidance:
                    sub["guidance"] = child.guidance
                if child.hint:
                    sub["hint"] = child.hint
                subparts.append(sub)
            entry["for_each"] = {"fields": subparts}
        out.append(entry)
    return out


def _parse_branch(data: Dict[str, Any], *, path: str) -> BranchDef:
    if not isinstance(data, dict):
        raise ValueError(f"Branch at {path} must be a mapping")
    reject_unknown_keys(data, _BRANCH_KEYS, path=path)
    return BranchDef(
        when=data.get("when", {}) or {},
        goto=data.get("goto", "") or "",
    )


def _parse_for_each_child(data: Dict[str, Any], *, path: str) -> FieldDef:
    if not isinstance(data, dict):
        raise ValueError(f"Field at {path} must be a mapping")
    reject_unknown_keys(data, _FOR_EACH_CHILD_FIELD_KEYS, path=path)
    validator = data.get("validator", "")
    if validator is not None and not isinstance(validator, str):
        raise ValueError(
            f"validator at {path} must be a function name string; "
            "use validator_args for parameters"
        )
    return FieldDef(
        key=str(data.get("key", "") or "").strip(),
        prompt=str(data.get("prompt", "") or ""),
        guidance=str(data.get("guidance", "") or ""),
        hint=str(data.get("hint", "") or ""),
        required=bool(data.get("required", True)),
        validator=str(validator or "").strip(),
        validator_args=dict(data.get("validator_args") or {}),
        pre_processor=parse_string_list(data.get("pre_processor")),
        post_processor=parse_string_list(data.get("post_processor")),
    )


def _parse_for_each(data: Any, *, path: str) -> ForEachDef:
    if not isinstance(data, dict):
        raise ValueError(f"for_each at {path} must be a mapping")
    reject_unknown_keys(data, _FOR_EACH_KEYS, path=path)
    raw_fields = data.get("fields") or []
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError(f"for_each.fields at {path} must be a non-empty list")
    children = [
        _parse_for_each_child(child, path=f"{path}.fields[{i}]")
        for i, child in enumerate(raw_fields)
    ]
    child_keys = [c.key for c in children]
    if len(child_keys) != len(set(child_keys)):
        raise ValueError(f"Duplicate for_each child keys at {path}")
    return ForEachDef(fields=children)


def _parse_field(data: Dict[str, Any], *, index: int) -> FieldDef:
    path = f"fields[{index}]"
    if not isinstance(data, dict):
        raise ValueError(f"Field at {path} must be a mapping")
    reject_unknown_keys(data, _FIELD_KEYS, path=path)
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
    for_each_raw = data.get("for_each")
    for_each = (
        _parse_for_each(for_each_raw, path=f"{path}.for_each") if for_each_raw else None
    )
    return FieldDef(
        key=str(data.get("key", "") or "").strip(),
        prompt=str(data.get("prompt", "") or "").strip(),
        guidance=str(data.get("guidance", "") or "").strip(),
        hint=str(data.get("hint", "") or "").strip(),
        required=bool(data.get("required", True)),
        validator=str(validator or "").strip(),
        validator_args=dict(data.get("validator_args") or {}),
        pre_processor=parse_string_list(data.get("pre_processor")),
        post_processor=parse_string_list(data.get("post_processor")),
        branches=branches,
        else_field=data.get("else"),
        for_each=for_each,
        for_each_prefix=str(data.get("for_each_prefix") or "").strip(),
    )


def _validate_for_each_child_keys(fields: List[FieldDef]) -> None:
    """Ensure for_each child keys do not collide with top-level field keys."""
    top_keys = {f.key for f in fields}
    for f in fields:
        if not f.for_each:
            continue
        for child in f.for_each.fields:
            if child.key in top_keys:
                raise ValueError(
                    f"for_each child key '{child.key}' on field '{f.key}' "
                    f"collides with top-level field key"
                )


def _parse_handlers(data: Any) -> HandlersDef:
    def _build(raw: Dict[str, Any]) -> HandlersDef:
        return HandlersDef(
            review=raw.get("review"),
            complete=raw.get("complete"),
            reset=raw.get("reset"),
            cancel=raw.get("cancel"),
        )

    return parse_handlers_mapping(
        data,
        allowed_keys=_HANDLER_KEYS,
        path="interview.handlers",
        builder=_build,
        string_fields=_HANDLER_KEYS,
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

    reject_unknown_keys(data, _INTERVIEW_KEYS, path="interview")

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
    _validate_for_each_child_keys(fields)
    skill_tools = parse_skill_tools(
        data.get("skill_tools"),
        path_prefix="interview",
        require_mapping=True,
    )

    parameters: List[Dict[str, Any]] = []
    for i, p in enumerate(data.get("parameters", []) or []):
        if not p:
            continue
        if not isinstance(p, dict):
            raise ValueError(f"parameters[{i}] must be a mapping")
        reject_unknown_keys(p, _PARAMETER_KEYS, path=f"interview.parameters[{i}]")
        if not p.get("response"):
            raise ValueError(f"interview.parameters[{i}].response is required")
        scope = str(p.get("scope", "response")).strip().lower()
        if scope not in ("response", "orchestration"):
            raise ValueError(
                f"interview.parameters[{i}].scope must be 'response' or 'orchestration', "
                f"got {scope!r}"
            )
        parameters.append(
            {
                "scope": scope,
                "condition": str(p.get("condition", "") or "").strip(),
                "response": str(p["response"]).strip(),
            }
        )

    return InterviewSpec(
        name=name,
        title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""),
        fields=fields,
        skill_tools=skill_tools,
        handlers=_parse_handlers(data.get("handlers")),
        confirm=_parse_confirm(data.get("confirm")),
        parameters=parameters,
        source_dir=source_dir,
    )


def load_interview_spec_from_skill(
    skill_dir: Union[str, Path],
) -> Optional[InterviewSpec]:
    """Load interview spec from ``SKILL.md`` frontmatter ``interview:`` block."""
    interview_data, default_name, _skill_file = load_frontmatter_block_from_skill(
        skill_dir,
        block_key=INTERVIEW_FRONTMATTER_KEY,
    )
    if interview_data is None:
        return None
    return parse_interview_spec(
        interview_data,
        source_dir=str(Path(skill_dir)),
        default_name=default_name,
    )


class InterviewRegistry(BaseSkillRegistry[InterviewSpec]):
    """Discovers, loads, and caches interview specs from skill directories."""

    def __init__(self) -> None:
        super().__init__(label="interview", loader=load_interview_spec_from_skill)
