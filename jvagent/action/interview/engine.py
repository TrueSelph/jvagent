"""Interview tool handlers — the full pipeline behind every interview__* tool.

Each handler is a plain async function taking the InterviewAction instance
first. The pipeline is deliberately thin: the model owns extraction and
chaining; the server validates, stores, runs configured hooks, and reports.
"""

from __future__ import annotations

import json
import logging
import re
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
    call_tool_directive,
    interview_tool_response,
    no_session_directive,
    restart_session_directive,
    review_confirmation_directive,
    slim_hook_entry,
    tell_user,
    tell_user_with_followup,
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
from .spec import (
    FieldDef,
    InterviewSpec,
    SkillToolDef,
    fields_reference,
)

logger = logging.getLogger(__name__)

SET_FIELDS_ARGS_EXAMPLE = (
    '{"fields": {"user_name": "Jane Doe", "available_times": "Monday at 9"}}'
)
_WORD_RE = re.compile(r"\b[a-z0-9_]{3,}\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"\b[a-z][a-z0-9_\-\s]{1,40}\s*(?:is|:|=)", re.IGNORECASE)
_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

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


def _batch_failure_status(failures: List[Dict[str, Any]], *, stored_any: bool) -> str:
    if stored_any:
        return "partial_success"
    if failures and all(
        failure.get("error_code") == "VALIDATION_FAILED" for failure in failures
    ):
        return "validation_failed"
    return "error"


def _batch_failure_directive(failures: List[Dict[str, Any]]) -> str:
    if not failures:
        return tell_user("Please share the missing information for this interview.")
    # A handler/validator-authored directive is written for the user — prefer it.
    if len(failures) == 1:
        direct = str(failures[0].get("response_directive") or "").strip()
        if direct:
            return direct
    # Name only genuinely-pending fields that failed validation. Unknown/guessed
    # keys are model errors (the model invented a field) and mean nothing to the
    # user. Field keys are humanized so the raw snake_case never shows.
    names = [
        str(f.get("field") or "").strip().replace("_", " ")
        for f in failures
        if f.get("error_code") == "VALIDATION_FAILED" and str(f.get("field") or "").strip()
    ]
    fields_text = ", ".join(name for name in names if name)
    # Surface only user-authored validation messages. Raw engine errors
    # (UNKNOWN_FIELD keys, "Awaiting keys: [...]") are model-facing context and
    # MUST NOT reach the user — they travel in system_message instead.
    user_error = next(
        (
            str(f.get("error") or "").strip()
            for f in failures
            if f.get("error_code") == "VALIDATION_FAILED"
            and str(f.get("error") or "").strip()
        ),
        "",
    )
    if fields_text:
        message = f"I still need valid values for: {fields_text}."
    else:
        message = "I still need a bit more information to continue."
    if user_error:
        message = f"{message} {user_error}"
    return tell_user(message)


def _append_directive_event(
    queue: List[Dict[str, Any]],
    *,
    field: Optional[str],
    stage: str,
    source: str,
    directive: Optional[str],
) -> None:
    text = str(directive or "").strip()
    if not text:
        return
    queue.append(
        {
            "field": field,
            "stage": stage,
            "source": source,
            "directive": text,
        }
    )


def _append_system_event(
    queue: List[Dict[str, Any]],
    *,
    field: Optional[str],
    stage: str,
    source: str,
    system_message: Optional[str],
) -> None:
    text = str(system_message or "").strip()
    if not text:
        return
    queue.append(
        {
            "field": field,
            "stage": stage,
            "source": source,
            "system_message": text,
        }
    )


def _normalize_user_directive_text(directive: str) -> str:
    text = str(directive or "").strip()
    lowered = text.lower()
    if lowered.startswith("tell the user:"):
        return text[len("Tell the user:") :].strip()
    if lowered.startswith("ask:"):
        return text[len("Ask:") :].strip()
    return text


def _compose_directives(
    queue: List[Dict[str, Any]],
    *,
    fallback: str,
) -> str:
    if not queue:
        return fallback

    user_parts: List[str] = []
    call_parts: List[str] = []
    for item in queue:
        directive = str(item.get("directive") or "").strip()
        if not directive:
            continue
        lowered = directive.lower()
        if lowered.startswith("call "):
            call_parts.append(directive)
            continue
        user_parts.append(_normalize_user_directive_text(directive))

    merged_user: List[str] = []
    for part in user_parts:
        text = part.strip()
        if not text or text in merged_user:
            continue
        merged_user.append(text)

    merged_calls: List[str] = []
    for call in call_parts:
        text = call.strip()
        if not text or text in merged_calls:
            continue
        merged_calls.append(text)

    if not merged_user and not merged_calls:
        return fallback

    if merged_user:
        base = f"Tell the user: {' '.join(merged_user)}"
    else:
        base = merged_calls.pop(0)

    for call in merged_calls:
        if call.lower().startswith("call "):
            base = f"{base} Then {call[0].lower() + call[1:]}"
        else:
            base = f"{base} Then {call}"
    return base


def _compose_system_message(
    queue: List[Dict[str, Any]],
    *,
    fallback: str = "",
) -> Optional[str]:
    parts: List[str] = []
    for item in queue:
        text = str(item.get("system_message") or "").strip()
        if not text or text in parts:
            continue
        parts.append(text)
    if not parts:
        return fallback or None
    return " ".join(parts)


def _compact_field_updates(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    updates: List[Dict[str, Any]] = []
    for item in results:
        entry: Dict[str, Any] = {
            "field": item.get("field"),
            "stored": bool(item.get("stored", False)),
        }
        if "value" in item:
            entry["value"] = item.get("value")
        if item.get("ignored"):
            entry["ignored"] = True
        if item.get("idempotent"):
            entry["idempotent"] = True
        if item.get("error"):
            entry["error"] = item.get("error")
        updates.append(entry)
    return updates


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
        return tell_user(fdef.prompt), extras

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
    return directive or tell_user(fdef.prompt), extras


async def run_pre_processors_for_store(
    action: Any,
    session: InterviewSession,
    spec: InterviewSpec,
    fdef: FieldDef,
    visitor: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run pre_processor hooks before validator/store inside set_fields."""
    entries: List[Dict[str, Any]] = []
    merged: Dict[str, Any] = {}
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
            "note",
            "next_tool",
            "interview_complete",
            "retain_context_keys",
            "system_message",
        ):
            if key in parsed:
                merged[key] = parsed[key]
    return entries, merged


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
            "note",
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


def _latest_user_text(visitor: Any = None) -> str:
    utterance = getattr(visitor, "utterance", None)
    if isinstance(utterance, str):
        return utterance.strip()
    interaction = getattr(visitor, "interaction", None) if visitor else None
    request = getattr(interaction, "request", None) if interaction else None
    text = getattr(request, "text", None) if request else None
    if isinstance(text, str):
        return text.strip()
    return ""


def _field_text_tokens(fdef: FieldDef) -> List[str]:
    source = " ".join(
        [fdef.key.replace("_", " "), fdef.prompt or "", fdef.guidance or ""]
    ).lower()
    return [token for token in _WORD_RE.findall(source) if len(token) >= 4]


def _under_extracted_candidate_keys(
    spec: InterviewSpec,
    field_map: Dict[str, str],
    visitor: Any = None,
) -> List[str]:
    """Detect likely under-extraction when a compound utterance yields one key."""
    if len(field_map) != 1:
        return []
    text = _latest_user_text(visitor)
    if not text:
        return []

    lower = text.lower()
    compound_signals = 0
    if " and " in lower:
        compound_signals += 1
    if "," in text or ";" in text:
        compound_signals += 1
    if len(_LABEL_RE.findall(text)) >= 2:
        compound_signals += 1
    if _EMAIL_RE.search(text):
        compound_signals += 1
    if _TIME_RE.search(text):
        compound_signals += 1
    if compound_signals < 2:
        return []

    submitted = set(field_map.keys())
    candidates: List[str] = []
    for fdef in spec.fields:
        if fdef.key in submitted:
            continue
        tokens = _field_text_tokens(fdef)
        if not tokens:
            continue
        if any(re.search(rf"\b{re.escape(token)}\b", lower) for token in tokens):
            candidates.append(fdef.key)
    return candidates


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
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="NO_FIELDS",
            error="No fields provided.",
            # Model-facing self-correction — how to re-call the tool. NOT a user
            # reply: this travels in system_message so it can never be relayed.
            system_message=(
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

    under_extracted_keys = _under_extracted_candidate_keys(spec, field_map, visitor)
    if under_extracted_keys:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="UNDER_EXTRACTED",
            error=(
                "Likely compound utterance detected, but set_fields payload contains "
                "only one key."
            ),
            submitted_fields=sorted(field_map.keys()),
            suggested_additional_keys=under_extracted_keys,
            # Model-facing self-correction — re-extract and retry. NOT a user
            # reply: travels in system_message so it can never be relayed.
            system_message=(
                "Extract all confident values from the latest user utterance and "
                "retry interview__set_fields with one complete fields map using "
                "known keys from field_keys/guidance_page/awaiting_fields."
            ),
        )

    load_fn = action._load_fn(spec)
    order = {key: idx for idx, key in enumerate(spec.field_keys())}
    ordered = sorted(
        field_map.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])
    )

    results: List[Dict[str, Any]] = []
    stored_any = False
    failures: List[Dict[str, Any]] = []
    post_outcomes: List[Dict[str, Any]] = []
    completion_candidates: List[Dict[str, Any]] = []
    directive_queue: List[Dict[str, Any]] = []
    system_queue: List[Dict[str, Any]] = []
    pruned_all: List[str] = []
    gate_ignored: List[str] = []
    note_queue: List[str] = []

    for fname, fvalue in ordered:
        fdef = spec.get_field(fname)
        if not fdef:
            awaiting_fields = await build_awaiting_fields(
                session, spec, load_fn, visitor, action
            )
            err, unknown_system = _unknown_field_error(fname, awaiting_fields)
            failure = {
                "field": fname,
                "error": err,
                "error_code": "UNKNOWN_FIELD",
            }
            if unknown_system:
                failure["system_message"] = unknown_system
            failures.append(failure)
            _append_system_event(
                system_queue,
                field=fname,
                stage="set",
                source="unknown_field",
                system_message=unknown_system,
            )
            results.append(
                {
                    "field": fname,
                    "stored": False,
                    "value": fvalue,
                    "error": err,
                }
            )
            continue

        # Incremental branch settlement: an earlier field in this same call may
        # have routed onto a branch that excludes this field. Skip it before
        # running its validator/post_processor so no off-path side effects fire.
        reachable_now = await compute_active_path_for_prune(
            session, spec, load_fn, visitor, action
        )
        if reachable_now and fname not in set(reachable_now):
            results.append(
                {
                    "field": fname,
                    "stored": False,
                    "value": fvalue,
                    "ignored": True,
                }
            )
            gate_ignored.append(fname)
            continue

        # Idempotency guard: a re-submitted field whose value exactly matches the
        # already-stored value is a no-op. Skip the pre_processor, validator, and
        # post_processor so their side effects (e.g. API lookups in post_processors)
        # do not re-fire when the model redundantly re-submits a collected field.
        # A genuine change (different value) falls through to normal processing,
        # so review corrections are unaffected.
        if session.has_field(fname) and str(fvalue) == str(session.get_value(fname)):
            results.append(
                {
                    "field": fname,
                    "stored": True,
                    "idempotent": True,
                    "value": session.get_value(fname),
                }
            )
            continue

        entry: Dict[str, Any] = {
            "field": fname,
            "stored": False,
            "value": fvalue,
        }

        if fdef.pre_processor:
            pre_entries, pre_merged = await run_pre_processors_for_store(
                action, session, spec, fdef, visitor
            )
            if pre_entries:
                for hook_entry in pre_entries:
                    _append_directive_event(
                        directive_queue,
                        field=fname,
                        stage="pre",
                        source=str(hook_entry.get("tool") or "pre_processor"),
                        directive=hook_entry.get("response_directive"),
                    )
                    _append_system_event(
                        system_queue,
                        field=fname,
                        stage="pre",
                        source=str(hook_entry.get("tool") or "pre_processor"),
                        system_message=hook_entry.get("system_message"),
                    )
            if pre_merged.get("note"):
                note_queue.append(str(pre_merged["note"]))
            if pre_merged.get("interview_complete"):
                completion_candidates.append({"field": fname, **pre_merged})

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
            failures.append(failure)
            _append_directive_event(
                directive_queue,
                field=fname,
                stage="validator",
                source=str(check.get("validator") or "validator"),
                directive=failure["response_directive"],
            )
            entry["error"] = err
            results.append(entry)
            continue

        stored_value = str(check.get("value", fvalue))
        session.set_value(fname, stored_value)
        stored_any = True
        entry["stored"] = True
        if check.get("response_directive"):
            _append_directive_event(
                directive_queue,
                field=fname,
                stage="validator",
                source=str(check.get("validator") or "validator"),
                directive=check.get("response_directive"),
            )
        if check.get("system_message"):
            _append_system_event(
                system_queue,
                field=fname,
                stage="validator",
                source=str(check.get("validator") or "validator"),
                system_message=check.get("system_message"),
            )
        if check.get("interview_complete"):
            completion_candidates.append({"field": fname, **check})

        if fdef.post_processor:
            hook_entries, merged = await run_post_processors(
                action, session, spec, fdef, visitor
            )
            if hook_entries:
                for hook_entry in hook_entries:
                    _append_directive_event(
                        directive_queue,
                        field=fname,
                        stage="post",
                        source=str(hook_entry.get("tool") or "post_processor"),
                        directive=hook_entry.get("response_directive"),
                    )
                    _append_system_event(
                        system_queue,
                        field=fname,
                        stage="post",
                        source=str(hook_entry.get("tool") or "post_processor"),
                        system_message=hook_entry.get("system_message"),
                    )
            if merged:
                post_outcomes.append({"field": fname, **merged})
            if merged.get("note"):
                note_queue.append(str(merged["note"]))
            if merged.get("interview_complete"):
                completion_candidates.append({"field": fname, **merged})

        results.append(entry)

    reachable = await compute_active_path_for_prune(
        session, spec, load_fn, visitor, action
    )
    if stored_any:
        pruned_all = prune_unreachable_fields(session, reachable)
    active_key_set = set(reachable)
    spec_keys = set(spec.field_keys())
    ignored_fields = {
        str(failure.get("field") or "")
        for failure in failures
        if str(failure.get("field") or "") in spec_keys
        and str(failure.get("field") or "") not in active_key_set
    }
    ignored_fields.update(name for name in gate_ignored if name in spec_keys)
    if ignored_fields:
        failures = [
            failure
            for failure in failures
            if str(failure.get("field") or "") not in ignored_fields
        ]
        for entry in results:
            field_name = str(entry.get("field") or "")
            if field_name in ignored_fields and not entry.get("stored"):
                entry["ok"] = True
                entry["ignored"] = True
                entry["error"] = None
                entry["validator"] = None
    pruned_set = set(pruned_all)

    if pruned_set or ignored_fields:
        filtered_fields = pruned_set | ignored_fields
        directive_queue = [
            item
            for item in directive_queue
            if not item.get("field") or item.get("field") not in filtered_fields
        ]
        system_queue = [
            item
            for item in system_queue
            if not item.get("field") or item.get("field") not in filtered_fields
        ]
        completion_candidates = [
            item
            for item in completion_candidates
            if not item.get("field") or item.get("field") not in filtered_fields
        ]

    filtered_fields = pruned_set | ignored_fields
    post_outcome: Dict[str, Any] = {}
    for item in post_outcomes:
        field_name = str(item.get("field") or "")
        if field_name and field_name in filtered_fields:
            continue
        post_outcome = item

    # A correction at review may reopen a gap on a different branch.
    if session.status == InterviewStatus.REVIEW:
        if await compute_missing_required(session, spec, load_fn, visitor, action):
            session.status = InterviewStatus.ACTIVE

    if stored_any:
        await action._save_session(session, visitor)

    complete_check = completion_candidates[-1] if completion_candidates else None
    if complete_check is not None and not failures:
        retain = complete_check.get("retain_context_keys") or []
        await action._clear_interview_session(visitor, retain_context_keys=retain)
        fallback_directive = str(complete_check.get("response_directive") or "").strip()
        directive = _compose_directives(
            directive_queue,
            fallback=fallback_directive or "Interview completed.",
        )
        system_message = _compose_system_message(
            system_queue,
            fallback=str(complete_check.get("system_message") or "").strip(),
        )
        return interview_tool_response(
            ok=True,
            status="completed",
            interview_complete=True,
            results=_compact_field_updates(results),
            pruned=pruned_all or None,
            response_directive=directive,
            system_message=system_message,
        )

    updates = _compact_field_updates(results)

    payload: Dict[str, Any] = {
        "ok": not failures,
        "status": session.status.value,
        "results": updates,
    }
    if pruned_all:
        payload["pruned"] = pruned_all
    if ignored_fields:
        payload["ignored"] = sorted(ignored_fields)
    if session.skipped_fields:
        payload["skipped_fields"] = sorted(session.skipped_fields)

    if failures:
        first_failure = failures[0]
        payload["status"] = _batch_failure_status(failures, stored_any=stored_any)
        # One clean directive from the failure set; per-field errors are in results[].
        payload["response_directive"] = _batch_failure_directive(failures)
        system_message = _compose_system_message(
            system_queue,
            fallback=str(first_failure.get("system_message") or "").strip(),
        )
        if system_message:
            payload["system_message"] = system_message
    else:
        # The next step is decided ONCE, from the FINAL settled state — not from
        # the per-field directives queued mid-batch (a later field in the same call
        # may have filled the field an earlier processor pointed at). Processor
        # notes are preserved and paired with that authoritative next step.
        next_field = await build_next_field(session, spec, load_fn, visitor, action)
        next_tool = post_outcome.get(
            "next_tool",
            "interview__next_field" if next_field else "interview__review",
        )
        notes_text = " ".join(n for n in note_queue if n).strip()
        inline_question = bool(notes_text and next_field)
        if inline_question:
            # Inlining the next question bypasses interview__next_field, so carry
            # what that tool would have provided: the canonical key (next_field_key
            # below) and, for optional fields, the skip path.
            directive = tell_user_with_followup(notes_text, next_field["prompt"])
            next_fdef = spec.get_field(next_field["key"])
            if next_fdef is not None and not next_fdef.required:
                directive += (
                    " If the user declines or has nothing to add, call "
                    f'interview__skip_field with {{"field_key": '
                    f'"{next_field["key"]}"}}.'
                )
            payload["response_directive"] = directive
        else:
            # No further questions — chain straight to review/complete with a
            # single, unambiguous tool call. A "Tell the user … then call" reply
            # is unreliable here: models tend to deliver the note and stop,
            # skipping the chained review (notably alongside a competing reply
            # directive such as a first-turn intro). Any pending note is carried
            # as system_message below so it survives without blocking the chain.
            payload["response_directive"] = call_tool_directive(next_tool)
        if next_field:
            payload["next_field_key"] = next_field["key"]
        if not inline_question:
            # next_tool signals a CHAIN: the model MUST call it before finalizing.
            # The inline-question branch above is a terminal reply (the question is
            # already in the directive), so it carries no next_tool — the directive
            # is delivered and the turn ends, preserving the note.
            payload["next_tool"] = next_tool
        system_message = _compose_system_message(
            system_queue,
            fallback=str(post_outcome.get("system_message") or "").strip(),
        )
        if notes_text and not next_field:
            # The note had no follow-up question to attach to; surface it as
            # context so the model can relay it when it presents the review.
            system_message = (
                f"{notes_text} {system_message}".strip()
                if system_message
                else notes_text
            )
        if system_message:
            payload["system_message"] = system_message

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
    next_field = await build_next_field(session, spec, load_fn, visitor, action)

    if not next_field:
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            skipped_fields=sorted(session.skipped_fields) or None,
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
            next_field={"key": next_field["key"], "prompt": next_field.get("prompt")},
            pre_tools_results=pre_tools_results,
        )

    slim_next = {
        "key": next_field["key"],
        "prompt": next_field.get("prompt"),
        "required": bool(fdef.required),
    }
    if extras.get("suggested_value") is not None:
        slim_next["suggested_value"] = extras["suggested_value"]

    # Optional fields are skippable — tell the model the skip path so a decline
    # routes to interview__skip_field instead of stalling.
    if not fdef.required:
        directive = (
            f"{directive} If the user declines or has nothing to add, call "
            f'interview__skip_field with {{"field_key": "{fdef.key}"}}.'
        )

    # Persist any session.context mutations made by pre_processor hooks.
    if pre_tools_results:
        await action._save_session(session, visitor)

    return interview_tool_response(
        ok=True,
        status=session.status.value,
        next_field=slim_next,
        skipped_fields=sorted(session.skipped_fields) or None,
        pre_tools_results=pre_tools_results or None,
        response_directive=directive,
    )


async def handle_skip_field(action: Any, field: str, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    field = (field or "").strip()
    if not field:
        # No field_key given — "skip" targets the current pending field. Resolve it
        # from the settled path so the model can skip with a bare call.
        load_fn = action._load_fn(spec)
        nxt = await build_next_field(session, spec, load_fn, visitor, action)
        field = str((nxt or {}).get("key") or "")

    if not field:
        # Nothing pending — the queue is already empty. This is not an error:
        # route cleanly to review so the terminal sequence (review → confirm →
        # complete) proceeds instead of looping on a skip/next_field thrash.
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            next_tool="interview__review",
            response_directive=call_tool_directive("interview__review"),
        )

    fdef = spec.get_field(field)
    if fdef is None:
        # Unknown field key — the model guessed a key from the prompt text
        # instead of using a real field_reference[].key (e.g. skipping
        # "training_availability_slot" when the field is "available_times").
        # Do NOT record a phantom skip: re-anchor on the actual pending field so
        # skipped_fields can never accumulate keys the spec doesn't define.
        load_fn = action._load_fn(spec)
        nxt = await build_next_field(session, spec, load_fn, visitor, action)
        if not nxt:
            return interview_tool_response(
                ok=False,
                status=session.status.value,
                error_code="UNKNOWN_FIELD",
                error=f"Unknown field '{field}'. No field is pending.",
                next_tool="interview__review",
                response_directive=call_tool_directive("interview__review"),
            )
        pending = spec.get_field(nxt["key"])
        prompt = nxt.get("prompt") or (
            f"Please provide your {nxt['key'].replace('_', ' ')}."
        )
        directive = tell_user(prompt)
        if pending is not None and not pending.required:
            directive += (
                " If the user declines or has nothing to add, call "
                f'interview__skip_field with {{"field_key": "{nxt["key"]}"}}.'
            )
        return interview_tool_response(
            ok=False,
            status=session.status.value,
            error_code="UNKNOWN_FIELD",
            error=(
                f"Unknown field '{field}'. Use the field_reference key — the "
                f"pending field is '{nxt['key']}'."
            ),
            next_field={
                "key": nxt["key"],
                "prompt": nxt.get("prompt"),
                "required": bool(pending.required) if pending else None,
            },
            response_directive=directive,
        )

    if fdef.required:
        question = fdef.prompt or f"Please provide your {field.replace('_', ' ')}."
        return interview_tool_response(
            ok=False,
            status=session.status.value,
            error=f"Field '{field}' is required and cannot be skipped.",
            response_directive=tell_user(question),
        )

    session.skip_field(field)
    load_fn = action._load_fn(spec)
    reachable = await compute_active_path_for_prune(
        session, spec, load_fn, visitor, action
    )
    prune_unreachable_fields(session, reachable)
    await action._save_session(session, visitor)

    directive, next_tool = await _chain_hint(action, session, spec, visitor)
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        field=field,
        skipped_fields=sorted(session.skipped_fields),
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

    if not func:
        summary = build_review_summary(
            session, spec, collected, visible_keys=visible_keys
        )
        session.status = InterviewStatus.REVIEW
        await action._save_session(session, visitor)
        return _review_response(session, spec, review_fields, summary)

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
            response_directive=tell_user(status_text),
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
    )


async def handle_complete(action: Any, visitor: Any = None) -> str:
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return _no_session_response()

    # Review gate (invariant #5): under manual confirmation the review step MUST
    # run before completion. Without this backstop a model that jumps straight to
    # interview__complete skips the confirmation summary AND closes the task —
    # the "skips review / drops the lock" failure. handle_review sets REVIEW, so
    # the legitimate review → confirm → complete path is unaffected. Auto-confirm
    # chains review→complete in one turn and is intentionally exempt.
    if spec.confirm != "auto" and session.status != InterviewStatus.REVIEW:
        return interview_tool_response(
            ok=False,
            status=session.status.value,
            error="Review required before completion.",
            next_tool="interview__review",
            response_directive=call_tool_directive("interview__review"),
        )

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
            else tell_user(raw_directive)
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
        response_directive=tell_user(cancel_message),
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
                response_directive=tell_user(
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

    return interview_tool_response(
        ok=True,
        status="restarted",
        response_directive=tell_user("No problem — let's start over."),
        next_tool="interview__next_field",
        system_message=call_tool_directive("interview__next_field"),
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
            directive = tell_user(str(directive))
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

    next_field = None
    if spec:
        load_fn = action._load_fn(spec)
        next_field = await build_next_field(session, spec, load_fn, visitor, action)

    return interview_tool_response(
        ok=True,
        status=session.status.value,
        interview_type=session.interview_type,
        fields=session.get_collected_summary(),
        skipped_fields=sorted(session.skipped_fields) or None,
        started_at=session.started_at,
        field_reference=fields_reference(spec) if spec else None,
        next_field_key=(next_field["key"] if next_field else None),
        confirm=spec.confirm if spec else None,
        custom_tools=(
            [f"{spec.name}__{t.name}" for t in spec.skill_tools] if spec else None
        ),
    )


async def interview_turn_status(action: Any, visitor: Any = None) -> Optional[str]:
    """Compact per-turn re-grounding for a locked interview.

    Activation sends the full ``field_reference`` once; on a resumed locked turn
    that observation may have aged out of history, so the model loses the valid
    keys and guesses (``full_name`` vs ``user_name``). This re-asserts just what
    key selection needs — valid keys, the pending field, and progress — without
    re-sending the whole catalog (guidance pages) every turn. ``get_status``
    remains the on-demand path for the full reference.
    """
    session, spec = await action._get_session_and_contract(visitor)
    if not session or not spec:
        return None
    load_fn = action._load_fn(spec)
    next_field = await build_next_field(session, spec, load_fn, visitor, action)
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        interview_type=session.interview_type,
        field_keys=spec.field_keys(),
        next_field=(
            {"key": next_field["key"], "prompt": next_field.get("prompt")}
            if next_field
            else None
        ),
        fields=session.get_collected_summary() or None,
        skipped_fields=sorted(session.skipped_fields) or None,
        confirm=spec.confirm,
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
        load_fn = action._load_fn(spec)
        next_field = await build_next_field(session, spec, load_fn, visitor, action)
        custom_tools = [f"{spec.name}__{t.name}" for t in spec.skill_tools]
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            interview_type=session.interview_type,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields) or None,
            field_reference=fields_reference(spec),
            start_field=(next_field["key"] if next_field else None),
            usage_note=(
                "field_reference is the full field catalog (key, prompt, guidance, "
                "required). It is sent once here; later tool results carry only "
                "outcomes and directives. Re-pull via interview__get_status if lost."
            ),
            confirm=spec.confirm,
            custom_tools=custom_tools or None,
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
