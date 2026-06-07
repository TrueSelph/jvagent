"""Field store pipeline: input_handler → validate → store → prune → post_tools."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..core.field_extractors import extract_candidates_for_question
from ..core.interview_loader import (
    InterviewSpec,
    QuestionDef,
    question_has_validator,
    resolve_validator_def,
    resolve_validator_kwargs,
)
from ..core.responses import (
    call_tool_directive,
    interview_tool_response,
    slim_post_tool_entry,
    tell_user_directive,
    validation_guidance_directive,
)
from ..core.session import CTX_QUESTION_PRESENTED, InterviewSession, InterviewStatus
from ..core.validators import ExtractionStatus
from .hooks import call_hook, load_hook_function
from .path_resolver import (
    build_next_questions,
    compute_reachable_question_names,
    compute_reachable_required,
    missing_required_reachable,
    prune_unreachable_fields,
    resolve_store_continuation,
)


async def merge_auto_review(
    action: "InterviewAction",
    visitor: Any,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Inline review into a store/skip response so the model does not re-call review."""
    review_raw = await action._handle_review(visitor)
    try:
        review = json.loads(review_raw)
    except (json.JSONDecodeError, TypeError):
        return payload
    if not review.get("ok", True):
        return payload
    for key in (
        "response_directive",
        "summary",
        "status",
        "fields",
        "skipped_fields",
        "custom_message",
        "system_message",
    ):
        if key in review:
            payload[key] = review[key]
    payload.pop("next_tool", None)
    payload["review_ready"] = True
    return payload


if TYPE_CHECKING:
    from ..interview_action import InterviewAction

logger = logging.getLogger(__name__)


async def run_input_handler(
    action: "InterviewAction",
    spec: InterviewSpec,
    question: QuestionDef,
    raw_value: str,
    session: InterviewSession,
    visitor: Any,
) -> tuple[str, Optional[str]]:
    """Return (normalized_value, optional_directive)."""
    if not question.input_handler:
        return raw_value, None
    func = load_hook_function(spec, question.input_handler)
    if not func:
        return raw_value, None
    result = await call_hook(
        func,
        session=session,
        spec=spec,
        visitor=visitor,
        interview_action=action,
        value=raw_value,
    )
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                result = parsed
        except (json.JSONDecodeError, TypeError):
            return result, None
    if isinstance(result, dict):
        val = result.get("value", raw_value)
        directive = result.get("directive") or result.get("response_directive")
        return str(val), directive
    if result is not None:
        return str(result), None
    return raw_value, None


def _supplied_grounded_in_utterance(supplied: str, utterance: str) -> bool:
    """True when model value is extracted from the user's message (not an override)."""
    s = (supplied or "").strip().lower()
    u = (utterance or "").strip().lower()
    if not s or not u or s == u:
        return False
    return s in u


def _utterance_candidates(
    question: QuestionDef,
    spec: InterviewSpec,
    utterance: str,
) -> List[str]:
    """Ordered unique values to try from the user's latest message."""
    msg = (utterance or "").strip()
    if not msg:
        return []
    vdef = resolve_validator_def(question, spec)
    if not vdef:
        return [msg]
    kwargs = resolve_validator_kwargs(question, vdef)
    candidates = [msg]
    for extracted in extract_candidates_for_question(question, vdef, msg, kwargs):
        if extracted not in candidates:
            candidates.append(extracted)
    return candidates


async def validate_field(
    action: "InterviewAction",
    spec: InterviewSpec,
    field_name: str,
    value: str,
    session: InterviewSession,
    visitor: Any,
) -> Dict[str, Any]:
    """Run the configured validator for one candidate value."""
    q = spec.get_question(field_name)
    if not q or not question_has_validator(q):
        return {"valid": True, "value": (value or "").strip()}

    vdef = resolve_validator_def(q, spec)
    if not vdef:
        return {
            "valid": False,
            "error": f"Validator not configured for field '{field_name}'",
            "validator": "",
        }

    kwargs = resolve_validator_kwargs(q, vdef)
    raw = await action._run_validator(vdef, value, kwargs, visitor, session, spec)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "valid": False,
            "error": f"Validator returned invalid JSON: {exc}",
            "validator": vdef.name,
        }

    if parsed.get("valid"):
        result: Dict[str, Any] = {
            "valid": True,
            "value": parsed.get("value", value),
            "validator": parsed.get("validator", vdef.name),
        }
        for key in ("interview_complete", "response_directive", "retain_context_keys"):
            if key in parsed:
                result[key] = parsed[key]
        return result
    result = {
        "valid": False,
        "error": parsed.get("error", f"Validation failed for {field_name}"),
        "validator": parsed.get("validator", vdef.name),
    }
    if "response_directive" in parsed:
        result["response_directive"] = parsed["response_directive"]
    return result


async def resolve_and_validate_field_value(
    action: "InterviewAction",
    spec: InterviewSpec,
    field_name: str,
    supplied_value: str,
    session: InterviewSession,
    visitor: Any,
) -> Dict[str, Any]:
    """Validate programmatically before store.

    When the visitor carries the user's latest utterance, validators run against
    that message (plus extractable substrings). A model-supplied ``value`` that
    disagrees with a failing utterance cannot bypass validation.
    """
    from jvagent.action.orchestrator.skill_tasks import visitor_utterance

    q = spec.get_question(field_name)
    if not q:
        return {"valid": False, "error": f"Unknown field '{field_name}'"}

    if not question_has_validator(q):
        resolved = (visitor_utterance(visitor) or supplied_value or "").strip()
        return {"valid": True, "value": resolved, "validator": None}

    utterance = visitor_utterance(visitor).strip() if visitor else ""
    supplied = (supplied_value or "").strip()

    if utterance:
        last_failure: Optional[Dict[str, Any]] = None
        for candidate in _utterance_candidates(q, spec, utterance):
            check = await validate_field(
                action, spec, field_name, candidate, session, visitor
            )
            if check.get("valid"):
                check["validated_from"] = "utterance"
                return check
            last_failure = check
        if supplied and _supplied_grounded_in_utterance(supplied, utterance):
            grounded = await validate_field(
                action, spec, field_name, supplied, session, visitor
            )
            if grounded.get("valid"):
                grounded["validated_from"] = "supplied_grounded"
                return grounded
        if last_failure:
            last_failure["validated_from"] = "utterance"
            return last_failure

    if supplied:
        check = await validate_field(
            action, spec, field_name, supplied, session, visitor
        )
        check["validated_from"] = "supplied"
        return check

    vdef = resolve_validator_def(q, spec)
    return {
        "valid": False,
        "error": f"No value provided for field '{field_name}'",
        "validator": vdef.name if vdef else "",
        "validated_from": "none",
    }


def build_validation_failed_payload(
    *,
    field_name: str,
    session: InterviewSession,
    check: Dict[str, Any],
    next_qs: List[Dict[str, Any]],
    missing: List[str],
) -> Dict[str, Any]:
    """Envelope returned when set_field validation fails — value is not stored."""
    err = check.get("error", "Invalid value")
    question_text = next_qs[0]["question"] if next_qs else ""
    directive = check.get("response_directive") or validation_guidance_directive(
        err, question_text=question_text
    )
    return {
        "ok": False,
        "stored": False,
        "status": "validation_failed",
        "valid": False,
        "error_code": "VALIDATION_FAILED",
        "error": err,
        "field": field_name,
        "validator": check.get("validator"),
        "validated_from": check.get("validated_from"),
        "fields": session.get_collected_summary(),
        "skipped_fields": sorted(session.skipped_fields),
        "missing_required": missing,
        "next_questions": next_qs,
        "response_directive": directive,
    }


async def run_post_tools(
    action: "InterviewAction",
    question_def: QuestionDef,
    session: InterviewSession,
    spec: InterviewSpec,
    visitor: Any,
    stored_value: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Run post_tools and merge results into payload."""
    post_results: List[Dict[str, Any]] = []
    last_directive: Optional[str] = None
    last_next_tool: Optional[str] = None

    for tool_name in question_def.post_tools:
        func = load_hook_function(spec, tool_name)
        if not func:
            continue
        try:
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=action,
            )
            parsed: Dict[str, Any] = {}
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    post_results.append(
                        {
                            "tool": tool_name,
                            "ok": False,
                            "error": "Invalid tool response",
                        }
                    )
                    continue
            elif isinstance(result, dict):
                parsed = result
            else:
                post_results.append(
                    {"tool": tool_name, "ok": False, "error": "Empty tool response"}
                )
                continue

            entry = slim_post_tool_entry(tool_name, parsed)
            post_results.append(entry)

            for key in (
                "skip_to_review",
                "next_tool",
                "present_field",
                "exists",
                "status",
                "interview_complete",
                "system_message",
                "error",
                "error_code",
                "response_directive",
            ):
                if key in parsed:
                    payload[key] = parsed[key]

            if parsed.get("skip_to_review"):
                last_next_tool = "interview__review"
                last_directive = call_tool_directive("interview__review")
            elif parsed.get("next_tool") == "interview__review":
                last_next_tool = parsed["next_tool"]
                last_directive = parsed.get(
                    "response_directive"
                ) or call_tool_directive(parsed["next_tool"])
            elif parsed.get("response_directive"):
                last_directive = parsed["response_directive"]
        except Exception as e:
            logger.error(
                "post_tools '%s' failed for question '%s': %s",
                tool_name,
                question_def.name,
                e,
            )
            post_results.append({"tool": tool_name, "ok": False, "error": str(e)})

    if post_results:
        payload["post_tools_results"] = post_results
        if any(not r.get("ok", True) for r in post_results):
            payload["ok"] = False

    if last_directive is not None:
        payload["response_directive"] = last_directive
    if last_next_tool is not None:
        payload["next_tool"] = last_next_tool
    elif payload.get("skip_to_review"):
        payload["next_tool"] = "interview__review"

    return payload


async def run_pre_tools(
    action: "InterviewAction",
    session: InterviewSession,
    spec: InterviewSpec,
    question_def: QuestionDef,
    visitor: Any = None,
) -> tuple[str, Dict[str, Any]]:
    """Run pre_tools before asking; return directive and extras."""
    extras: Dict[str, Any] = {}
    pre_tools = question_def.resolved_pre_tools()
    pre_results: List[Dict[str, Any]] = []

    if not pre_tools:
        return tell_user_directive(question_def.question), extras

    directive: Optional[str] = None
    for tool_name in pre_tools:
        func = load_hook_function(spec, tool_name)
        if not func:
            continue
        try:
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=action,
            )
            parsed: Dict[str, Any] = {}
            if isinstance(result, dict):
                parsed = result
            elif isinstance(result, str):
                try:
                    parsed = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    pre_results.append(
                        {
                            "tool": tool_name,
                            "ok": False,
                            "error": "Invalid tool response",
                        }
                    )
                    continue

            tool_ok = parsed.get("ok", True) if parsed else False
            if parsed:
                pre_results.append({"tool": tool_name, "ok": tool_ok, **parsed})
                suggested = parsed.get("suggested_value")
                if suggested is None:
                    suggested = parsed.get("value")
                if suggested is not None:
                    extras["suggested_value"] = suggested
                if parsed.get("directive"):
                    directive = parsed["directive"]
                elif parsed.get("response_directive"):
                    directive = parsed["response_directive"]
        except Exception as e:
            logger.error(
                "pre_tools '%s' failed for question '%s': %s",
                tool_name,
                question_def.name,
                e,
            )
            pre_results.append({"tool": tool_name, "ok": False, "error": str(e)})

    extras["pre_tools_results"] = pre_results
    if directive:
        return directive, extras
    return tell_user_directive(question_def.question), extras


async def apply_store_pipeline(
    action: "InterviewAction",
    session: InterviewSession,
    spec: InterviewSpec,
    field_name: str,
    raw_value: str,
    visitor: Any,
) -> Dict[str, Any]:
    """Full set_field pipeline; returns payload dict (not JSON string)."""

    def load_fn(fn: str):
        return load_hook_function(spec, fn)

    q = spec.get_question(field_name)
    value = raw_value

    if q and q.input_handler:
        value, handler_directive = await run_input_handler(
            action, spec, q, raw_value, session, visitor
        )
        if handler_directive:
            return {
                "ok": False,
                "stored": False,
                "status": "handler_directive",
                "field": field_name,
                "value": value,
                "response_directive": handler_directive,
            }

    check = await resolve_and_validate_field_value(
        action, spec, field_name, value, session, visitor
    )
    if not check.get("valid"):
        next_qs = await build_next_questions(
            session,
            spec,
            load_fn,
            visitor,
            action,
        )
        required = await compute_reachable_required(
            session,
            spec,
            load_fn,
            visitor,
            action,
        )
        return build_validation_failed_payload(
            field_name=field_name,
            session=session,
            check=check,
            next_qs=next_qs,
            missing=missing_required_reachable(session, required),
        )

    stored_value = check.get("value", value)
    session.set_value(field_name, stored_value)
    if isinstance(session.context, dict):
        session.context.pop(CTX_QUESTION_PRESENTED, None)

    reachable = await compute_reachable_question_names(
        session,
        spec,
        load_fn,
        visitor,
        action,
    )
    prune_unreachable_fields(session, reachable)

    if session.status == InterviewStatus.REVIEW:
        required = await compute_reachable_required(
            session,
            spec,
            load_fn,
            visitor,
            action,
        )
        if missing_required_reachable(session, required):
            session.status = InterviewStatus.ACTIVE

    await action._save_session(session, visitor)

    required = await compute_reachable_required(
        session,
        spec,
        load_fn,
        visitor,
        action,
    )
    missing = missing_required_reachable(session, required)

    payload: Dict[str, Any] = {
        "ok": True,
        "stored": True,
        "status": session.status.value,
        "field": field_name,
        "value": stored_value,
        "validator": check.get("validator"),
        "validated_from": check.get("validated_from"),
        "fields": session.get_collected_summary(),
        "skipped_fields": sorted(session.skipped_fields),
        "missing_required": missing,
    }

    if check.get("interview_complete"):
        payload["interview_complete"] = True
        if check.get("response_directive"):
            payload["response_directive"] = check["response_directive"]
        retain = check.get("retain_context_keys") or []
        await action._clear_interview_session(visitor, retain_context_keys=retain)
        return payload

    if q and q.post_tools:
        payload = await run_post_tools(
            action, q, session, spec, visitor, stored_value, payload
        )
        if payload.get("skip_to_review") or payload.get("interview_complete"):
            return payload

        cont_directive, cont_next_tool = await resolve_store_continuation(
            session, spec, load_fn, visitor, action
        )
        if not payload.get("next_tool") and not payload.get("present_field"):
            payload["next_tool"] = cont_next_tool
        if not payload.get("response_directive"):
            payload["response_directive"] = cont_directive

        present_field = payload.get("present_field")
        if present_field:
            if not isinstance(session.context, dict):
                session.context = {}
            session.context[CTX_QUESTION_PRESENTED] = present_field
            await action._save_session(session, visitor)

        if payload.get("next_tool") == "interview__review":
            return await merge_auto_review(action, visitor, payload)
        if payload.get("next_tool") or payload.get("response_directive"):
            return payload
        if payload.get("post_tools_results"):
            return payload

    directive, next_tool = await resolve_store_continuation(
        session, spec, load_fn, visitor, action
    )
    if not payload.get("response_directive"):
        payload["response_directive"] = directive
    if not payload.get("next_tool"):
        payload["next_tool"] = next_tool

    if payload.get("next_tool") == "interview__review":
        return await merge_auto_review(action, visitor, payload)

    return payload
