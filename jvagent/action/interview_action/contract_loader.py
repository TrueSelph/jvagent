"""Contract loader — discovers and loads contract.yaml from skill directories.

Each contract.yaml declares the questions, validators, tools, and completion
handler for an interview.  The LLM reads the SKILL.md procedure and uses the
tools exposed by InterviewAction to conduct the interview, choosing
which validator, API call, or data operation to invoke at each step.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)


ValidatorSpec = Union[str, Dict[str, Any]]


@dataclass
class QuestionDef:
    name: str
    question: str
    description: str = ""
    required: bool = True
    validator: ValidatorSpec = ""
    validator_kwargs: Dict[str, Any] = field(default_factory=dict)
    input_context_provider: Optional[str] = None
    pre_tools: List[str] = field(default_factory=list)
    post_tools: List[str] = field(default_factory=list)

    def resolved_pre_tools(self) -> List[str]:
        """pre_tools list, or input_context_provider as a single entry."""
        if self.pre_tools:
            return list(self.pre_tools)
        if self.input_context_provider:
            return [self.input_context_provider]
        return []


@dataclass
class ValidatorDef:
    name: str
    description: str = ""
    kwargs: Dict[str, Any] = field(default_factory=dict)


def _resolve_validator_name(spec: Dict[str, Any], fallback: str = "") -> str:
    """Map YAML validator spec to ValidatorDef.name (function name)."""
    if spec.get("name") == "builtin":
        return spec.get("function", "")
    return spec.get("function") or spec.get("name") or fallback


@dataclass
class ToolParamDef:
    name: str = ""
    type: str = "string"
    description: str = ""


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
class InterviewContract:
    name: str
    title: str = ""
    description: str = ""
    questions: List[QuestionDef] = field(default_factory=list)
    validators: List[ValidatorDef] = field(default_factory=list)
    tools: List[ToolDef] = field(default_factory=list)
    completion: Optional[CompletionDef] = None
    review: Optional[ReviewDef] = None
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


def _validator_spec_to_def(
    spec: ValidatorSpec,
    contract: InterviewContract,
    fallback_name: str = "",
) -> Optional[ValidatorDef]:
    """Resolve a question validator spec (inline dict or registry name) to ValidatorDef."""
    if not spec:
        return None
    if isinstance(spec, dict):
        return ValidatorDef(
            name=_resolve_validator_name(spec, fallback_name),
            description=spec.get("description", ""),
            kwargs=spec.get("kwargs", {}),
        )
    if isinstance(spec, str):
        return contract.get_validator(spec)
    return None


def resolve_validator_def(
    question: QuestionDef,
    contract: InterviewContract,
) -> Optional[ValidatorDef]:
    """Resolve the primary validator for a question."""
    return _validator_spec_to_def(
        question.validator, contract, fallback_name=question.name
    )


def resolve_validator_kwargs(
    question: QuestionDef,
    vdef: Optional[ValidatorDef],
) -> Dict[str, Any]:
    """Merge inline, registry, and question-level validator kwargs."""
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


def _parse_question(data: Dict[str, Any]) -> QuestionDef:
    return QuestionDef(
        name=data.get("name", ""),
        question=data.get("question", ""),
        description=data.get("description", ""),
        required=data.get("required", True),
        validator=data.get("validator", ""),
        validator_kwargs=data.get("validator_kwargs", {}),
        input_context_provider=data.get("input_context_provider"),
        pre_tools=_parse_string_list(data.get("pre_tools")),
        post_tools=_parse_string_list(data.get("post_tools")),
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


def load_contract(yaml_path: str) -> InterviewContract:
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    questions = [_parse_question(q) for q in data.get("questions", [])]
    validators = [_parse_validator(v) for v in data.get("validators", [])]
    tools = [_parse_tool(t) for t in data.get("tools", [])]
    completion = (
        _parse_completion(data.get("completion", {}))
        if data.get("completion")
        else None
    )
    review = _parse_review(data.get("review", {})) if data.get("review") else None

    return InterviewContract(
        name=data.get("name", ""),
        title=data.get("title", ""),
        description=data.get("description", ""),
        questions=questions,
        validators=validators,
        tools=tools,
        completion=completion,
        review=review,
        source_dir=str(Path(yaml_path).parent),
    )


class ContractRegistry:
    """Discovers, loads, and caches contract.yaml from skill directories."""

    def __init__(self):
        self._contracts: Dict[str, InterviewContract] = {}

    def discover(self, skills_dirs: List[str]) -> Dict[str, InterviewContract]:
        for skills_dir in skills_dirs:
            skills_path = Path(skills_dir)
            if not skills_path.is_dir():
                continue
            for skill_dir in skills_path.iterdir():
                if not skill_dir.is_dir():
                    continue
                contract_yaml = skill_dir / "contract.yaml"
                if contract_yaml.exists():
                    try:
                        contract = load_contract(str(contract_yaml))
                        self._contracts[contract.name] = contract
                        logger.info(
                            f"Loaded interview contract: {contract.name} from {contract_yaml}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to load contract from {contract_yaml}: {e}"
                        )
        return self._contracts

    def get(self, name: str) -> Optional[InterviewContract]:
        return self._contracts.get(name)

    def list_contracts(self) -> List[str]:
        return list(self._contracts.keys())

    def reload(self, skills_dirs: List[str]) -> Dict[str, InterviewContract]:
        self._contracts.clear()
        return self.discover(skills_dirs)
