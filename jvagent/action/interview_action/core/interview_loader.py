"""Interview spec loader — discovers structured interview config from skill directories.

Canonical source: ``interview:`` block in ``SKILL.md`` frontmatter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

SKILL_MD = "SKILL.md"
INTERVIEW_FRONTMATTER_KEY = "interview"

ValidatorSpec = Union[str, Dict[str, Any]]


@dataclass
class BranchDef:
    condition: Dict[str, Any] = field(default_factory=dict)
    target: str = ""


@dataclass
class QuestionDef:
    name: str
    question: str
    description: str = ""
    required: bool = True
    validator: ValidatorSpec = ""
    validator_kwargs: Dict[str, Any] = field(default_factory=dict)
    input_handler: Optional[str] = None
    pre_tools: List[str] = field(default_factory=list)
    post_tools: List[str] = field(default_factory=list)
    branches: List[BranchDef] = field(default_factory=list)
    default_next: Optional[str] = None

    def resolved_pre_tools(self) -> List[str]:
        return list(self.pre_tools)


@dataclass
class ValidatorDef:
    name: str
    description: str = ""
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDef:
    name: str
    description: str = ""
    function: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionDef:
    function: Optional[str] = None
    description: str = ""


@dataclass
class ReviewDef:
    function: Optional[str] = None
    description: str = ""


@dataclass
class ResetDef:
    function: Optional[str] = None
    description: str = ""


@dataclass
class InterviewSpec:
    name: str
    title: str = ""
    description: str = ""
    questions: List[QuestionDef] = field(default_factory=list)
    validators: List[ValidatorDef] = field(default_factory=list)
    tools: List[ToolDef] = field(default_factory=list)
    completion: Optional[CompletionDef] = None
    review: Optional[ReviewDef] = None
    reset: Optional[ResetDef] = None
    cancel: Optional[CompletionDef] = None
    source_dir: str = ""

    def get_required_fields(self) -> List[str]:
        return [q.name for q in self.questions if q.required]

    def get_question(self, name: str) -> Optional[QuestionDef]:
        for q in self.questions:
            if q.name == name:
                return q
        return None

    def get_validator(self, name: str) -> Optional[ValidatorDef]:
        for v in self.validators:
            if v.name == name:
                return v
        return None

    def get_tool(self, name: str) -> Optional[ToolDef]:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def question_names(self) -> List[str]:
        return [q.name for q in self.questions]


def _resolve_validator_name(spec: Dict[str, Any], fallback: str = "") -> str:
    return spec.get("function") or spec.get("name") or fallback


def _validator_spec_to_def(
    spec: ValidatorSpec,
    interview_spec: InterviewSpec,
    fallback_name: str = "",
) -> Optional[ValidatorDef]:
    if not spec:
        return None
    if isinstance(spec, dict):
        return ValidatorDef(
            name=_resolve_validator_name(spec, fallback_name),
            description=spec.get("description", ""),
            kwargs=spec.get("kwargs", {}),
        )
    if isinstance(spec, str):
        return interview_spec.get_validator(spec)
    return None


def resolve_validator_def(
    question: QuestionDef,
    interview_spec: InterviewSpec,
) -> Optional[ValidatorDef]:
    return _validator_spec_to_def(
        question.validator, interview_spec, fallback_name=question.name
    )


def question_has_validator(question: QuestionDef) -> bool:
    """True when the question declares a validator in frontmatter."""
    spec = question.validator
    if not spec:
        return False
    if isinstance(spec, str):
        return bool(spec.strip())
    if isinstance(spec, dict):
        return bool(spec.get("function") or spec.get("name"))
    return False


def resolve_validator_kwargs(
    question: QuestionDef,
    vdef: Optional[ValidatorDef],
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if vdef and vdef.kwargs:
        kwargs.update(vdef.kwargs)
    if isinstance(question.validator, dict):
        inline_kwargs = question.validator.get("kwargs", {})
        if inline_kwargs:
            kwargs.update(inline_kwargs)
    if question.validator_kwargs:
        kwargs.update(question.validator_kwargs)
    return kwargs


def _parse_string_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    return []


def _parse_branch(data: Dict[str, Any]) -> BranchDef:
    return BranchDef(
        condition=data.get("condition", {}) or {},
        target=data.get("target", "") or "",
    )


def _parse_question(data: Dict[str, Any]) -> QuestionDef:
    branches = [_parse_branch(b) for b in data.get("branches", []) or []]
    return QuestionDef(
        name=data.get("name", ""),
        question=data.get("question", ""),
        description=data.get("description", ""),
        required=data.get("required", True),
        validator=data.get("validator", ""),
        validator_kwargs=data.get("validator_kwargs", {}),
        input_handler=data.get("input_handler"),
        pre_tools=_parse_string_list(data.get("pre_tools")),
        post_tools=_parse_string_list(data.get("post_tools")),
        branches=branches,
        default_next=data.get("default_next"),
    )


def _parse_validator(data: Dict[str, Any]) -> ValidatorDef:
    return ValidatorDef(
        name=_resolve_validator_name(data),
        description=data.get("description", ""),
        kwargs=data.get("kwargs", {}),
    )


def _parse_tool(data: Dict[str, Any]) -> ToolDef:
    return ToolDef(
        name=data.get("name", ""),
        description=data.get("description", ""),
        function=data.get("function", ""),
        parameters=data.get("parameters", {}),
    )


def _parse_completion(data: Dict[str, Any]) -> CompletionDef:
    return CompletionDef(
        function=data.get("function"),
        description=data.get("description", ""),
    )


def _parse_review(data: Dict[str, Any]) -> ReviewDef:
    return ReviewDef(
        function=data.get("function"),
        description=data.get("description", ""),
    )


def _parse_reset(data: Dict[str, Any]) -> ResetDef:
    return ResetDef(
        function=data.get("function"),
        description=data.get("description", ""),
    )


def parse_interview_spec(
    data: Dict[str, Any],
    *,
    source_dir: str,
    default_name: str = "",
) -> InterviewSpec:
    """Build ``InterviewSpec`` from a parsed mapping (frontmatter)."""
    if not isinstance(data, dict):
        raise ValueError("Interview spec must be a YAML mapping")

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

    questions = [_parse_question(q) for q in data.get("questions", []) or []]
    validators = [_parse_validator(v) for v in data.get("validators", []) or []]
    tools = [_parse_tool(t) for t in data.get("tools", []) or []]
    completion = (
        _parse_completion(data.get("completion", {}))
        if data.get("completion")
        else None
    )
    review = _parse_review(data.get("review", {})) if data.get("review") else None
    reset = _parse_reset(data.get("reset", {})) if data.get("reset") else None
    cancel = _parse_completion(data.get("cancel", {})) if data.get("cancel") else None

    return InterviewSpec(
        name=name,
        title=data.get("title", ""),
        description=data.get("description", ""),
        questions=questions,
        validators=validators,
        tools=tools,
        completion=completion,
        review=review,
        reset=reset,
        cancel=cancel,
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


def _load_spec_from_skill_dir(skill_dir: Path) -> Optional[InterviewSpec]:
    """Load interview spec from SKILL.md frontmatter."""
    if not (skill_dir / SKILL_MD).is_file():
        return None
    try:
        return load_interview_spec_from_skill(skill_dir)
    except Exception as exc:
        logger.error(
            "Failed to load interview spec from %s frontmatter: %s",
            skill_dir / SKILL_MD,
            exc,
        )
        return None


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
                if not skill_dir.is_dir():
                    continue
                try:
                    spec = _load_spec_from_skill_dir(skill_dir)
                except Exception as e:
                    logger.error(
                        "Failed to load interview spec from %s: %s",
                        skill_dir,
                        e,
                    )
                    continue
                if spec is None or not spec.name:
                    continue
                self._specs[spec.name] = spec
                logger.info(
                    "Loaded interview spec: %s from %s",
                    spec.name,
                    skill_dir,
                )
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
