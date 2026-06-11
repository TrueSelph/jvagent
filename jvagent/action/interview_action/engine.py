"""Interview tool handlers — the full pipeline behind every interview__* tool.

Each handler is a plain async function taking the InterviewAction instance
first. The pipeline is deliberately thin: the model owns extraction and
chaining; the server validates, stores, runs configured hooks, and reports.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from jvagent.tooling.tool_executor import get_dispatch_visitor

from . import tasks
from .flow import (
    build_awaiting_fields,
    build_next_field,
    compute_active_path_for_prune,
    compute_missing_required,
    compute_review_field_keys,
    prune_unreachable_fields,
    resolve_next_field_name,
)
from .hooks import call_hook, coerce_hook_result, load_hook_function, run_validator
from .responses import (
    auto_confirm_directive,
    build_field_awareness_message,
    call_tool_directive,
    interview_tool_response,
    no_session_directive,
    restart_session_directive,
    review_confirmation_directive,
    slim_hook_entry,
    tell_user_directive,
    validation_guidance_directive,
)
from .session import (
    InterviewSession,
    InterviewStatus,
    clear_interview_context,
    clear_session,
    load_session,
    save_session,
)
from .spec import FieldDef, InterviewSpec, SkillToolDef, field_def_to_dict

logger = logging.getLogger(__name__)

SET_FIELDS_ARGS_EXAMPLE = (
    '{"fields": {"user_name": "Jane Doe", "available_times": "Monday at 9"}}'
)

# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------


async def get_conversation(visitor: Any = None):
    if visitor is None:
        visitor = get_dispatch_visitor()
    if visitor is None:
        return None
    if getattr(visitor, "conversation", None) is not None:
        return visitor.conversation
    interaction = getattr(visitor, "interaction", None)
    if interaction is not None and hasattr(interaction, "get_conversation"):
        return await interaction.get_conversation()
    return None


async def get_session(visitor: Any = None) -> Optional[InterviewSession]:
    conversation = await get_conversation(visitor)
    return load_session(conversation) if conversation else None


async def save_session_for(visitor: Any, session: InterviewSession) -> None:
    conversation = await get_conversation(visitor)
    if conversation:
        await save_session(conversation, session)


async def clear_interview_session(
    visitor: Any = None,
    *,
    retain_context_keys: Optional[List[str]] = None,
) -> None:
    conversation = await get_conversation(visitor)
    if not conversation:
        return
    clear_interview_context(conversation, retain_keys=retain_context_keys)
    try:
        await conversation.save()
    except Exception:
        pass


async def get_session_and_spec(
    action: Any, visitor: Any = None
) -> Tuple[Optional[InterviewSession], Optional[InterviewSpec]]:
    await action._ensure_specs_loaded()
    session = await get_session(visitor)
    if not session:
        return None, None
    return session, action._registry.get(session.interview_type)


def _no_session_response() -> str:
    return interview_tool_response(
        ok=False,
        status="error",
        error_code="NO_SESSION",
        error="No active interview session.",
        response_directive=no_session_directive(),
    )


_FIELD_AWARENESS_ACTION = "InterviewAction"
_FIELD_AWARENESS_PREFIX = "Awaiting user input for "


async def record_field_awareness(visitor: Any, message: str) -> None:
    """Persist the latest field-awareness snapshot on the current interaction.

    Upserts a single InterviewAction field-awareness event per interaction so
    prior-turn history does not accumulate stale awaiting-field lines from
    multiple tool calls or turn-prep snapshots.
    """
    text = (message or "").strip()
    if not text:
        return
    interaction = getattr(visitor, "interaction", None) if visitor else None
    if interaction is None:
        return
    events = getattr(interaction, "events", None)
    if events is None:
        interaction.events = []
        events = interaction.events

    for idx in range(len(events) - 1, -1, -1):
        event = events[idx]
        if not isinstance(event, dict):
            continue
        if event.get("action_name") != _FIELD_AWARENESS_ACTION:
            continue
        content = str(event.get("content", ""))
        if not content.startswith(_FIELD_AWARENESS_PREFIX):
            continue
        if content == text:
            return
        events[idx] = {"action_name": _FIELD_AWARENESS_ACTION, "content": text}
        try:
            await interaction.save()
        except Exception as exc:
            logger.debug("record_field_awareness: save failed: %s", exc)
        return

    if not interaction.add_event(text, _FIELD_AWARENESS_ACTION):
        return
    try:
        await interaction.save()
    except Exception as exc:
        logger.debug("record_field_awareness: save failed: %s", exc)


async def _session_field_context(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    visitor: Any = None,
) -> Dict[str, Any]:
    """Branch-aware missing required keys and unanswered collectible fields."""
    load_fn = action._load_fn(spec)
    missing = await compute_missing_required(session, spec, load_fn, visitor, action)
    awaiting = await build_awaiting_fields(session, spec, load_fn, visitor, action)
    awareness = build_field_awareness_message(awaiting)
    return {
        "missing_required": missing,
        "awaiting_fields": awaiting,
        "field_awareness": awareness or None,
    }


async def _session_field_context_and_record(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    visitor: Any = None,
    *,
    record: bool = True,
) -> Dict[str, Any]:
    ctx = await _session_field_context(action, session, spec, visitor)
    if record:
        await record_field_awareness(visitor, ctx.get("field_awareness") or "")
    return ctx


def _unknown_field_error(
    fname: str, awaiting_fields: List[Dict[str, Any]]
) -> Tuple[str, Optional[str]]:
    """Build UNKNOWN_FIELD message and optional system_message."""
    awaiting_keys = [str(f.get("key", "")) for f in awaiting_fields if f.get("key")]
    err = f"Unknown field '{fname}'. Awaiting keys: {awaiting_keys}"
    system_message: Optional[str] = None
    if len(awaiting_keys) == 1:
        system_message = (
            f'Use set_fields key "{awaiting_keys[0]}" (see awaiting_fields), '
            f'not "{fname}".'
        )
    return err, system_message


# ---------------------------------------------------------------------------
# Pre / post processor hooks
# ---------------------------------------------------------------------------


async def run_pre_processors(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    fdef: FieldDef,
    visitor: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """Run pre_processor hooks before asking; return (directive, extras)."""
    extras: Dict[str, Any] = {}
    if not fdef.pre_processor:
        return tell_user_directive(fdef.prompt), extras

    results: List[Dict[str, Any]] = []
    directive: Optional[str] = None
    for tool_name in fdef.pre_processor:
        func = load_hook_function(spec, tool_name)
        if not func:
            continue
        try:
            parsed = coerce_hook_result(
                await call_hook(
                    func,
                    session=session,
                    spec=spec,
                    visitor=visitor,
                    interview_action=action,
                )
            )
        except Exception as e:
            logger.error(
                "pre_processor '%s' failed for field '%s': %s", tool_name, fdef.key, e
            )
            results.append({"tool": tool_name, "ok": False, "error": str(e)})
            continue
        if parsed:
            results.append({"tool": tool_name, "ok": parsed.get("ok", True), **parsed})
            suggested = parsed.get("suggested_value", parsed.get("value"))
            if suggested is not None:
                extras["suggested_value"] = suggested
            if parsed.get("response_directive"):
                directive = parsed.get("response_directive")

    extras["pre_tools_results"] = results
    return directive or tell_user_directive(fdef.prompt), extras


async def run_post_processors(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    fdef: FieldDef,
    visitor: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run post_processor hooks after a successful store.

    Returns (slim result entries, merged outcome). The merged outcome may carry
    ``response_directive``, ``next_tool``, ``interview_complete``,
    ``retain_context_keys``, and ``system_message`` — directives are queued on
    the tool response for the model's next reply.
    """
    entries: List[Dict[str, Any]] = []
    merged: Dict[str, Any] = {}
    for tool_name in fdef.post_processor:
        func = load_hook_function(spec, tool_name)
        if not func:
            continue
        try:
            parsed = coerce_hook_result(
                await call_hook(
                    func,
                    session=session,
                    spec=spec,
                    visitor=visitor,
                    interview_action=action,
                )
            )
        except Exception as e:
            logger.error(
                "post_processor '%s' failed for field '%s': %s", tool_name, fdef.key, e
            )
            entries.append({"tool": tool_name, "ok": False, "error": str(e)})
            continue
        if not parsed:
            entries.append(
                {"tool": tool_name, "ok": False, "error": "Empty tool response"}
            )
            continue
        entries.append(slim_hook_entry(tool_name, parsed))
        for key in (
            "response_directive",
            "next_tool",
            "interview_complete",
            "retain_context_keys",
            "system_message",
        ):
            if key in parsed:
                merged[key] = parsed[key]
    return entries, merged


# ---------------------------------------------------------------------------
# Field store pipeline
# ---------------------------------------------------------------------------


def _normalize_field_map(
    fields: Optional[Dict[str, str]],
    **kwargs: Any,
) -> Dict[str, str]:
    """Coerce tool args to a field map. Canonical shape: ``{"fields": {...}}``."""
    if not fields or not isinstance(fields, dict):
        return {}
    return {str(k): str(v) for k, v in fields.items() if v is not None}


async def _chain_hint(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    visitor: Any = None,
) -> Tuple[str, str]:
    """Next mechanical step after a successful store or skip: (directive, next_tool)."""
    next_name = await resolve_next_field_name(
        session, spec, action._load_fn(spec), visitor, action
    )
    next_tool = "interview__next_field" if next_name else "interview__review"
    return call_tool_directive(next_tool), next_tool


async def handle_set_fields(
    action: Any,
    fields: Optional[Dict[str, str]] = None,
    visitor: Any = None,
    **kwargs: Any,
) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    field_map = _normalize_field_map(fields, **kwargs)
    if not field_map:
        field_ctx = await _session_field_context_and_record(
            action, session, spec, visitor
        )
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="NO_FIELDS",
            error="No fields provided.",
            **field_ctx,
            response_directive=tell_user_directive(
                "Call interview__set_fields with a single `fields` map — e.g. "
                f"{SET_FIELDS_ARGS_EXAMPLE}. Do not put field keys at the top "
                "level of args."
            ),
        )

    if session.status == InterviewStatus.COMPLETED:
        return interview_tool_response(
            status="completed",
            response_directive=restart_session_directive(session.interview_type),
        )

    load_fn = action._load_fn(spec)
    order = {key: idx for idx, key in enumerate(spec.field_keys())}
    ordered = sorted(
        field_map.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])
    )

    results: List[Dict[str, Any]] = []
    stored_any = False
    failure: Optional[Dict[str, Any]] = None
    post_outcome: Dict[str, Any] = {}
    complete_check: Optional[Dict[str, Any]] = None
    pruned_all: List[str] = []

    for fname, fvalue in ordered:
        fdef = spec.get_field(fname)
        if not fdef:
            field_ctx = await _session_field_context_and_record(
                action, session, spec, visitor
            )
            awaiting_fields = field_ctx["awaiting_fields"]
            err, unknown_system = _unknown_field_error(fname, awaiting_fields)
            failure = {
                "field": fname,
                "error": err,
                "error_code": "UNKNOWN_FIELD",
                "awaiting_fields": awaiting_fields,
                "field_awareness": field_ctx.get("field_awareness"),
            }
            if unknown_system:
                failure["system_message"] = unknown_system
            results.append({"field": fname, "ok": False, "stored": False, "error": err})
            break

        check = await run_validator(action, spec, fdef, fvalue, session, visitor)
        if not check.get("valid"):
            err = check.get("error", "Invalid value")
            failure = {
                "field": fname,
                "error": err,
                "error_code": "VALIDATION_FAILED",
                "validator": check.get("validator"),
                "response_directive": check.get("response_directive")
                or validation_guidance_directive(err, question_text=fdef.prompt),
            }
            results.append(
                {
                    "field": fname,
                    "ok": False,
                    "stored": False,
                    "error": err,
                    "validator": check.get("validator"),
                }
            )
            break

        stored_value = str(check.get("value", fvalue))
        session.set_value(fname, stored_value)
        stored_any = True
        entry: Dict[str, Any] = {
            "field": fname,
            "ok": True,
            "stored": True,
            "value": stored_value,
        }
        if check.get("validator"):
            entry["validator"] = check["validator"]
        results.append(entry)

        reachable = await compute_active_path_for_prune(
            session, spec, load_fn, visitor, action
        )
        pruned = prune_unreachable_fields(session, reachable)
        if pruned:
            entry["pruned_fields"] = pruned
            pruned_all.extend(pruned)

        if check.get("interview_complete"):
            complete_check = check
            break

        if fdef.post_processor:
            hook_entries, merged = await run_post_processors(
                action, session, spec, fdef, visitor
            )
            if hook_entries:
                entry["post_tools_results"] = hook_entries
            post_outcome.update(merged)
            if merged.get("interview_complete"):
                complete_check = merged
                break

    # A correction at review may reopen a gap on a different branch.
    if session.status == InterviewStatus.REVIEW:
        if await compute_missing_required(session, spec, load_fn, visitor, action):
            session.status = InterviewStatus.ACTIVE

    if stored_any:
        await action._save_session(session, visitor)

    if complete_check is not None:
        retain = complete_check.get("retain_context_keys") or []
        fields_summary = session.get_collected_summary()
        await action._clear_interview_session(visitor, retain_context_keys=retain)
        last_post = results[-1].get("post_tools_results") if results else None
        return interview_tool_response(
            ok=True,
            status="completed",
            interview_complete=True,
            results=results,
            post_tools_results=last_post,
            fields=fields_summary,
            response_directive=complete_check.get("response_directive"),
            system_message=complete_check.get("system_message"),
        )

    if failure is None:
        field_ctx = await _session_field_context_and_record(
            action, session, spec, visitor
        )
    else:
        field_ctx = await _session_field_context(action, session, spec, visitor)
        if failure.get("error_code") == "UNKNOWN_FIELD":
            field_ctx["field_awareness"] = failure.get("field_awareness")
    payload: Dict[str, Any] = {
        "ok": failure is None,
        "status": session.status.value,
        "results": results,
        "fields": session.get_collected_summary(),
        "skipped_fields": sorted(session.skipped_fields),
        **field_ctx,
    }
    if pruned_all:
        payload["pruned_fields"] = pruned_all
    if len(results) == 1:
        payload["field"] = results[0]["field"]
        payload["stored"] = results[0]["stored"]
        if "value" in results[0]:
            payload["value"] = results[0]["value"]
        if "post_tools_results" in results[0]:
            payload["post_tools_results"] = results[0]["post_tools_results"]

    if failure:
        payload["status"] = (
            "validation_failed"
            if failure["error_code"] == "VALIDATION_FAILED"
            else "error"
        )
        payload["field"] = failure["field"]
        payload["error"] = failure["error"]
        payload["error_code"] = failure["error_code"]
        if failure.get("validator"):
            payload["validator"] = failure["validator"]
        if failure.get("response_directive"):
            payload["response_directive"] = failure["response_directive"]
        if failure.get("system_message"):
            payload["system_message"] = failure["system_message"]
    else:
        directive, next_tool = await _chain_hint(action, session, spec, visitor)
        payload["response_directive"] = post_outcome.get(
            "response_directive", directive
        )
        payload["next_tool"] = post_outcome.get("next_tool", next_tool)
        if post_outcome.get("system_message"):
            payload["system_message"] = post_outcome["system_message"]

    ok = payload.pop("ok")
    status = payload.pop("status")
    return interview_tool_response(ok=ok, status=status, **payload)


async def persist_interview_fields(
    action: Any,
    session: InterviewSession,
    visitor: Any,
    fields: Dict[str, str],
    *,
    validate: bool = True,
) -> Dict[str, Any]:
    """Hook-initiated store used by custom skill tools."""
    spec = action._registry.get(session.interview_type)
    if not spec:
        return {
            "stored": [],
            "stored_values": {},
            "validation_errors": {"_session": "No spec found for interview type"},
        }
    stored: List[str] = []
    stored_values: Dict[str, str] = {}
    validation_errors: Dict[str, str] = {}
    for name, raw_value in fields.items():
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        fdef = spec.get_field(name)
        if not fdef:
            validation_errors[name] = f"Unknown field '{name}'"
            continue
        if validate:
            check = await run_validator(action, spec, fdef, value, session, visitor)
            if not check.get("valid"):
                validation_errors[name] = check.get("error", "Validation failed")
                continue
            value = str(check.get("value", value))
        session.set_value(name, value)
        stored.append(name)
        stored_values[name] = value
    if stored:
        load_fn = action._load_fn(spec)
        reachable = await compute_active_path_for_prune(
            session, spec, load_fn, visitor, action
        )
        prune_unreachable_fields(session, reachable)
        if session.status == InterviewStatus.REVIEW:
            if await compute_missing_required(session, spec, load_fn, visitor, action):
                session.status = InterviewStatus.ACTIVE
        await action._save_session(session, visitor)
    return {
        "stored": stored,
        "stored_values": stored_values,
        "validation_errors": validation_errors,
    }


# ---------------------------------------------------------------------------
# Flow handlers
# ---------------------------------------------------------------------------


async def handle_next_field(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    load_fn = action._load_fn(spec)
    field_ctx = await _session_field_context_and_record(action, session, spec, visitor)
    next_field = await build_next_field(session, spec, load_fn, visitor, action)

    if not next_field:
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            **field_ctx,
            next_tool="interview__review",
            response_directive=call_tool_directive("interview__review"),
        )

    fdef = spec.get_field(next_field["key"])
    directive, extras = await run_pre_processors(action, session, spec, fdef, visitor)
    pre_tools_results = extras.get("pre_tools_results") or []
    if any(not r.get("ok", True) for r in pre_tools_results):
        return interview_tool_response(
            ok=False,
            status="error",
            error="One or more pre_processor hooks failed.",
            fields=session.get_collected_summary(),
            **field_ctx,
            next_field=next_field,
            pre_tools_results=pre_tools_results,
        )
    if extras.get("suggested_value") is not None:
        next_field = dict(next_field)
        next_field["suggested_value"] = extras["suggested_value"]

    # Persist any session.context mutations made by pre_processor hooks.
    if pre_tools_results:
        await action._save_session(session, visitor)

    return interview_tool_response(
        ok=True,
        status=session.status.value,
        fields=session.get_collected_summary(),
        skipped_fields=sorted(session.skipped_fields),
        **field_ctx,
        next_field=next_field,
        pre_tools_results=pre_tools_results or None,
        response_directive=directive,
    )


async def handle_skip_field(action: Any, field: str, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    if not (field or "").strip():
        return interview_tool_response(
            ok=False,
            status=session.status.value,
            error="field_key is required.",
            response_directive=tell_user_directive(
                "Call interview__skip_field with "
                '{"field_key": "field_name"} for the optional field to skip.'
            ),
        )

    fdef = spec.get_field(field)
    if fdef and fdef.required:
        question = fdef.prompt or f"Please provide your {field.replace('_', ' ')}."
        return interview_tool_response(
            ok=False,
            status=session.status.value,
            error=f"Field '{field}' is required and cannot be skipped.",
            response_directive=tell_user_directive(question),
        )

    session.skip_field(field)
    load_fn = action._load_fn(spec)
    reachable = await compute_active_path_for_prune(
        session, spec, load_fn, visitor, action
    )
    prune_unreachable_fields(session, reachable)
    await action._save_session(session, visitor)

    field_ctx = await _session_field_context_and_record(action, session, spec, visitor)
    directive, next_tool = await _chain_hint(action, session, spec, visitor)
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        field=field,
        fields=session.get_collected_summary(),
        skipped_fields=sorted(session.skipped_fields),
        **field_ctx,
        response_directive=directive,
        next_tool=next_tool,
    )


# ---------------------------------------------------------------------------
# Review / complete
# ---------------------------------------------------------------------------


def build_review_summary(
    session: InterviewSession,
    spec: InterviewSpec,
    collected: Dict[str, str],
    *,
    visible_keys: Optional[List[str]] = None,
    omit_fields: Optional[set] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Build review markdown — only stored fields on the active path."""
    omitted = omit_fields or set()
    keys = visible_keys if visible_keys is not None else list(collected.keys())
    by_key = {f.key: f for f in spec.fields}
    lines = []
    for key in keys:
        if key in omitted or session.is_skipped(key):
            continue
        if key not in collected:
            continue
        fdef = by_key.get(key)
        label = (
            key.replace("_", " ").title()
            if fdef is None
            else fdef.key.replace("_", " ").title()
        )
        lines.append(f"**{label}**: {collected[key]}")
    for label, value in (additional_data or {}).items():
        lines.append(f"**{label}**: {value}")
    return "\n\n".join(lines)


def _review_response(
    session: InterviewSession,
    spec: InterviewSpec,
    collected: Dict[str, str],
    summary: str,
    *,
    preamble: str = "",
    custom_message: str = "",
    field_ctx: Optional[Dict[str, Any]] = None,
) -> str:
    auto = spec.confirm == "auto"
    if auto:
        directive = auto_confirm_directive(summary, preamble=preamble)
    elif preamble and not preamble.strip().startswith("Tell the user:"):
        directive = review_confirmation_directive(summary, preamble=preamble)
    else:
        directive = review_confirmation_directive(summary)

    payload: Dict[str, Any] = {
        "ok": True,
        "status": "review",
        "response_directive": directive,
        "fields": collected,
        "skipped_fields": sorted(session.skipped_fields),
        "summary": summary,
        "confirm": spec.confirm,
        "custom_message": custom_message or None,
    }
    if field_ctx:
        payload.update(field_ctx)
    if auto:
        payload["next_tool"] = "interview__complete"
        payload["system_message"] = (
            "Auto-confirm mode — call interview__complete in this same turn."
        )
    else:
        payload["system_message"] = (
            "Confirmation step — wait for user to confirm before interview__complete."
        )
    ok = payload.pop("ok")
    status = payload.pop("status")
    return interview_tool_response(ok=ok, status=status, **payload)


async def handle_review(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    collected = session.get_collected_summary()
    visible_keys = await compute_review_field_keys(
        session, spec, action._load_fn(spec), visitor, action
    )
    review_fields = {k: collected[k] for k in visible_keys if k in collected}
    review_fn = spec.handlers.review
    func = load_hook_function(spec, review_fn) if review_fn else None

    field_ctx = await _session_field_context(action, session, spec, visitor)
    if not func:
        summary = build_review_summary(
            session, spec, collected, visible_keys=visible_keys
        )
        session.status = InterviewStatus.REVIEW
        await action._save_session(session, visitor)
        return _review_response(
            session, spec, review_fields, summary, field_ctx=field_ctx
        )

    try:
        result = await call_hook(
            func, session=session, spec=spec, visitor=visitor, interview_action=action
        )
    except Exception as e:
        return interview_tool_response(
            ok=False,
            status="error",
            error=f"Custom review function failed: {e}",
            response_directive=f"Custom review function failed: {e}",
        )

    omit_fields: set = set()
    additional_data: Dict[str, Any] = {}
    custom_message = ""
    directive = ""
    terminate = False

    if isinstance(result, dict):
        modified_values = result.get("modified_values", {}) or {}
        additional_data = result.get("additional_data", {}) or {}
        custom_message = result.get("custom_message", "")
        directive = result.get("response_directive", "")
        terminate = bool(
            result.get("terminate") or modified_values.get("__terminate__") == "true"
        )
        for field_name, field_value in modified_values.items():
            if field_name == "__terminate__":
                continue
            if field_value == "__omit__":
                omit_fields.add(field_name)
            elif field_name in collected:
                collected[field_name] = field_value

    if terminate:
        if visitor:
            await tasks.close_task(visitor, status="completed", spec_name=spec.name)
        await action._clear_interview_session(visitor)
        status_text = custom_message or directive or "Share the status update."
        return interview_tool_response(
            ok=True,
            status="completed",
            terminate=True,
            response_directive=tell_user_directive(status_text),
            fields=review_fields,
            skipped_fields=sorted(session.skipped_fields),
            custom_message=custom_message or None,
        )

    summary = build_review_summary(
        session,
        spec,
        collected,
        visible_keys=visible_keys,
        omit_fields=omit_fields,
        additional_data=additional_data,
    )
    session.status = InterviewStatus.REVIEW
    await action._save_session(session, visitor)
    review_fields = {
        k: collected[k] for k in visible_keys if k in collected and k not in omit_fields
    }
    return _review_response(
        session,
        spec,
        review_fields,
        summary,
        preamble=custom_message or directive,
        custom_message=custom_message,
        field_ctx=field_ctx,
    )


async def handle_complete(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    fields_summary = session.get_collected_summary()
    complete_fn = spec.handlers.complete
    if not complete_fn:
        await action._clear_interview_session(visitor)
        if visitor:
            await tasks.close_task(visitor, status="completed", spec_name=spec.name)
        return interview_tool_response(
            ok=True,
            status="completed",
            response_directive="Interview completed successfully.",
            fields=fields_summary,
        )

    func = load_hook_function(spec, complete_fn)
    if not func:
        return interview_tool_response(
            ok=False,
            status="error",
            error=f"Completion function '{complete_fn}' not found.",
            response_directive=f"Completion function '{complete_fn}' not found.",
        )
    try:
        result = await call_hook(
            func, session=session, spec=spec, visitor=visitor, interview_action=action
        )
    except Exception as e:
        return interview_tool_response(
            ok=False,
            status="error",
            error=f"Completion function failed: {e}",
            response_directive=f"Completion function failed: {e}",
        )

    if visitor:
        await tasks.close_task(visitor, status="completed", spec_name=spec.name)

    retain_keys: List[str] = []
    if isinstance(result, dict):
        raw_retain = result.get("retain_context_keys")
        if isinstance(raw_retain, list):
            retain_keys = [str(k) for k in raw_retain if k]
    await action._clear_interview_session(visitor, retain_context_keys=retain_keys)

    if isinstance(result, dict):
        raw_directive = result.get("response_directive") or "Interview completed."
        stripped = raw_directive.strip()
        directive = (
            raw_directive
            if stripped.startswith("Tell the user:") or stripped.startswith("Call ")
            else tell_user_directive(raw_directive)
        )
        return interview_tool_response(
            ok=True,
            status="completed",
            response_directive=directive,
            completion_result=result,
            fields=fields_summary,
        )
    return interview_tool_response(
        ok=True,
        status="completed",
        response_directive="Interview completed successfully.",
        fields=fields_summary,
    )


# ---------------------------------------------------------------------------
# Cancel / reset / status / start
# ---------------------------------------------------------------------------


async def handle_cancel(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session:
        return interview_tool_response(
            ok=False,
            status="error",
            error="No active interview session to cancel.",
            response_directive="No active interview session to cancel.",
        )

    cancel_message = (
        "I've cancelled this. Say what you'd like to do next, or start a new "
        "interview when you're ready."
    )
    cancel_fn = spec.handlers.cancel if spec else None
    func = load_hook_function(spec, cancel_fn) if spec and cancel_fn else None
    if func:
        try:
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=action,
            )
            if isinstance(result, dict):
                cancel_message = result.get("response_directive") or cancel_message
        except Exception as e:
            logger.error("Cancel handler failed: %s", e)

    await action._clear_interview_session(visitor)
    if visitor:
        await tasks.close_task(
            visitor, status="cancelled", spec_name=spec.name if spec else None
        )
    return interview_tool_response(
        ok=True,
        status="cancelled",
        response_directive=tell_user_directive(cancel_message),
        fields={},
    )


async def handle_reset(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="NO_SESSION",
            error="No active interview session to reset.",
            response_directive="No active interview session to reset.",
        )

    reset_fn = spec.handlers.reset
    func = load_hook_function(spec, reset_fn) if reset_fn else None
    if func:
        try:
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=action,
            )
        except Exception as e:
            logger.error("Custom reset handler failed: %s", e)
            return interview_tool_response(
                ok=False,
                status="error",
                error=f"Custom reset handler failed: {e}",
                response_directive=tell_user_directive(
                    "I couldn't reset the interview. Say when you'd like to try again."
                ),
            )
        coerced = _coerce_reset_hook_result(result)
        if coerced is not None:
            return coerced

    # Default reset: clear collected answers in place; session and task stay open.
    session.fields.clear()
    session.skipped_fields.clear()
    session.context.clear()
    session.status = InterviewStatus.ACTIVE
    await action._save_session(session, visitor)

    field_ctx = await _session_field_context_and_record(action, session, spec, visitor)
    return interview_tool_response(
        ok=True,
        status="restarted",
        response_directive=tell_user_directive("No problem — let's start over."),
        next_tool="interview__next_field",
        system_message=call_tool_directive("interview__next_field"),
        **field_ctx,
    )


def _coerce_reset_hook_result(result: Any) -> Optional[str]:
    if isinstance(result, str):
        try:
            json.loads(result)
            return result
        except json.JSONDecodeError:
            return None
    if isinstance(result, dict):
        directive = result.get("response_directive")
        status = str(result.get("status") or "restarted")
        ok = result.get("ok")
        if ok is None:
            ok = status not in ("error", "validation_failed")
        if directive and not str(directive).startswith("Tell the user:"):
            directive = tell_user_directive(str(directive))
        return interview_tool_response(
            ok=bool(ok),
            status=status,
            response_directive=directive,
            system_message=result.get("system_message"),
        )
    return None


async def handle_get_status(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session:
        available = action._registry.list_specs()
        return interview_tool_response(
            ok=False,
            status="no_session",
            available_types=available,
            response_directive=(
                "No active interview session. Available types: " + ", ".join(available)
                if available
                else "No interview types configured."
            ),
        )

    field_ctx = (
        await _session_field_context(action, session, spec, visitor) if spec else {}
    )
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        interview_type=session.interview_type,
        fields=session.get_collected_summary(),
        skipped_fields=sorted(session.skipped_fields),
        started_at=session.started_at,
        **field_ctx,
        field_definitions=(
            [field_def_to_dict(f) for f in spec.fields] if spec else None
        ),
        confirm=spec.confirm if spec else None,
        custom_tools=(
            [f"{spec.name}__{t.name}" for t in spec.skill_tools] if spec else None
        ),
    )


async def handle_start(
    action: Any,
    interview_type: str,
    visitor: Any = None,
    **kwargs: Any,
) -> str:
    spec = action._registry.get(interview_type)
    if not spec:
        available = action._registry.list_specs()
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="UNKNOWN_INTERVIEW_TYPE",
            response_directive=(
                f"Interview type '{interview_type}' not found. "
                f"Available types: {available}"
            ),
            available_types=available,
        )

    conversation = await action._get_conversation(visitor)
    existing = load_session(conversation) if conversation else None

    async def _session_envelope(session: InterviewSession, **extra: Any) -> str:
        field_ctx = await _session_field_context_and_record(
            action, session, spec, visitor
        )
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            interview_type=session.interview_type,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            **field_ctx,
            confirm=spec.confirm,
            custom_tools=[f"{spec.name}__{t.name}" for t in spec.skill_tools],
            **extra,
        )

    if existing and existing.is_active() and existing.interview_type == interview_type:
        if visitor:
            await tasks.ensure_active_task(visitor, spec, action.description)
        return await _session_envelope(existing)

    fresh_session = existing is None
    if conversation and existing:
        if visitor and existing.interview_type != interview_type:
            await tasks.close_task(
                visitor, status="cancelled", spec_name=existing.interview_type
            )
        clear_session(conversation)
        fresh_session = True

    session = InterviewSession(interview_type=interview_type)
    if conversation:
        await save_session(conversation, session)
    if visitor:
        await tasks.ensure_active_task(visitor, spec, action.description)

    return await _session_envelope(session, fresh_session=fresh_session)


# ---------------------------------------------------------------------------
# Custom skill tools
# ---------------------------------------------------------------------------


async def handle_custom_tool(
    action: Any, tdef: SkillToolDef, spec: InterviewSpec, **kwargs: Any
) -> str:
    if not tdef.function:
        return json.dumps({"error": f"Custom tool '{tdef.name}' has no function"})
    func = load_hook_function(spec, tdef.function)
    if not func:
        return json.dumps({"error": f"Function '{tdef.function}' not found"})
    try:
        visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
        session = await action._get_session(visitor)
        call_kwargs = dict(kwargs)
        call_kwargs["visitor"] = visitor
        result = await call_hook(
            func,
            session=session,
            spec=spec,
            visitor=visitor,
            interview_action=action,
            kwargs=call_kwargs,
        )
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return await _finalize_tool_response(
                        action, parsed, session, visitor
                    )
            except (json.JSONDecodeError, TypeError):
                pass
            return result
        if isinstance(result, dict):
            return await _finalize_tool_response(action, result, session, visitor)
        return json.dumps(
            {"result": "ok"} if result is not None else {"result": "empty"}
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _finalize_tool_response(
    action: Any,
    parsed: Dict[str, Any],
    session: Optional[InterviewSession],
    visitor: Any,
) -> str:
    persist_fields = parsed.get("persist_fields")
    if persist_fields and session and isinstance(persist_fields, dict):
        await persist_interview_fields(
            action, session, visitor, persist_fields, validate=True
        )
    if session:
        spec = action._registry.get(session.interview_type)
        if spec:
            parsed.setdefault("fields", session.get_collected_summary())
            parsed.setdefault("skipped_fields", sorted(session.skipped_fields))
            parsed.setdefault(
                "missing_required",
                await compute_missing_required(
                    session, spec, action._load_fn(spec), visitor, action
                ),
            )
    return json.dumps(parsed)
