"""Shared constants and serialization helpers for InterviewAction."""

from __future__ import annotations

import json
from typing import Any, Dict

from .core.interview_loader import QuestionDef, ValidatorDef
from .core.validators import ExtractionStatus

TASK_OWNER_ACTION = "InterviewAction"
TASK_TYPE = "INTERVIEW"

ACTIVE_TASK_DESCRIPTION_TEMPLATE = (
    "The user has engaged the {action_title} (Action Description: {action_description}). "
    "If their latest message is off-topic or unrelated to it, answer that in at most one "
    "short sentence, then steer back and continue the interview — always "
    "ending your reply with the current pending question. Do not abandon the {action_title} until it is "
    "complete or the user explicitly cancels."
)


def _parse_validation_result(
    result: Any, original_value: str, validator_name: str
) -> Dict[str, Any]:
    if isinstance(result, dict):
        if result.get("valid") is True:
            out: Dict[str, Any] = {
                "valid": True,
                "value": result.get("value", original_value),
                "validator": validator_name,
            }
            for key in ("interview_complete", "response_directive"):
                if key in result:
                    out[key] = result[key]
            return out
        out = {
            "valid": False,
            "error": result.get("error", f"Validation failed for {validator_name}"),
            "value": original_value,
            "validator": validator_name,
        }
        if "response_directive" in result:
            out["response_directive"] = result["response_directive"]
        return out
    if isinstance(result, tuple) and len(result) == 3:
        status, error_msg, autocorrected = result
        if status == ExtractionStatus.EXTRACTED:
            return {
                "valid": True,
                "value": autocorrected or original_value,
                "validator": validator_name,
            }
        return {
            "valid": False,
            "error": error_msg or f"Validation failed for {validator_name}",
            "validator": validator_name,
        }
    if isinstance(result, str):
        try:
            as_json = json.loads(result)
            if isinstance(as_json, dict) and "valid" in as_json:
                return _parse_validation_result(as_json, original_value, validator_name)
        except (json.JSONDecodeError, TypeError):
            return {"valid": True, "value": result, "validator": validator_name}
    return {
        "valid": False,
        "error": f"Unexpected validation result type: {type(result)}",
        "validator": validator_name,
    }


def _question_def_to_dict(q: QuestionDef) -> Dict[str, Any]:
    result = {
        "name": q.name,
        "question": q.question,
        "description": q.description,
        "required": q.required,
        "validator": q.validator,
    }
    if q.validator_kwargs:
        result["validator_kwargs"] = q.validator_kwargs
    if q.input_handler:
        result["input_handler"] = q.input_handler
    if q.pre_tools:
        result["pre_tools"] = q.pre_tools
    if q.post_tools:
        result["post_tools"] = q.post_tools
    if q.branches:
        result["branches"] = [
            {"condition": b.condition, "target": b.target} for b in q.branches
        ]
    if q.default_next:
        result["default_next"] = q.default_next
    return result


def _validator_def_to_dict(v: ValidatorDef) -> Dict[str, Any]:
    result: Dict[str, Any] = {"function": v.name, "description": v.description}
    if v.kwargs:
        result["kwargs"] = v.kwargs
    return result
