"""Leadgen tool handlers — capture, retrieve, status, sync."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from jvagent.tooling.tool_executor import get_tool_visitor

from . import sync as sync_mod
from .hooks import HookExecutionContext, call_hook
from .spec import LeadGenSpec, fields_reference
from .store import LeadRecord
from .validators import locale_validator, run_validator

if TYPE_CHECKING:
    from .leadgen_action import LeadGenAction

logger = logging.getLogger(__name__)

_LAST_CAPTURE: Dict[tuple, float] = {}
_CAPTURE_DEDUP_TTL = 30.0


async def get_user_and_interaction(visitor: Any = None):
    visitor = visitor or get_tool_visitor()
    interaction = getattr(visitor, "interaction", None)
    if not interaction:
        return None, None
    user = await interaction.get_user()
    return user, interaction


def resolve_spec(
    action: "LeadGenAction", skill_name: Optional[str] = None
) -> Optional[LeadGenSpec]:
    if skill_name:
        return action._registry.get(skill_name)
    specs = list(action._registry.specs.values())
    if len(specs) == 1:
        return specs[0]
    if specs:
        return specs[0]
    return None


def merge_spec_with_action_defaults(
    action: "LeadGenAction", spec: Optional[LeadGenSpec]
) -> LeadGenSpec:
    from .spec import FieldDef, LeadGenSpec, SyncDef, SyncMode

    if spec is not None:
        merged = spec
    else:
        merged = LeadGenSpec(name="_default")

    if action.default_fields and not merged.fields:
        for k, v in action.default_fields.items():
            cfg = v if isinstance(v, dict) else {}
            merged.fields.append(
                FieldDef(
                    key=k,
                    guidance=str(cfg.get("guidance", "") or ""),
                    required=bool(cfg.get("required", False)),
                    aliases=list(cfg.get("aliases") or []),
                    validator=str(cfg.get("validator", "") or ""),
                    decline_value=cfg.get("decline_value"),
                    merge=bool(cfg.get("merge", False)),
                    capture_only=bool(cfg.get("capture_only", False)),
                )
            )

    # When the skill spec declares no sync destinations, the action-level sync
    # config governs the whole sync (mode + thresholds + destinations) — this is
    # what lets deployments keep sync entirely in agent.yaml. A skill that DOES
    # declare its own destinations keeps full control of its sync block.
    if action.sync_destinations and not merged.sync.destinations:
        raw_mode = action.sync_mode
        if raw_mode not in ("on_capture", "on_complete", "manual"):
            raw_mode = "on_capture"
        mode = cast(SyncMode, raw_mode)
        merged.sync = SyncDef(
            mode=mode,
            min_fields=list(action.sync_min_fields),
            require_any=list(action.sync_require_any),
            destinations=list(action.sync_destinations),
        )
    return merged


def canonicalize_fields(raw: Dict[str, Any], spec: LeadGenSpec) -> Dict[str, Any]:
    alias_map = spec.alias_map()
    out: Dict[str, Any] = {}
    known = set(spec.field_keys())
    for key, value in raw.items():
        if key.startswith("_"):
            out[key] = value
            continue
        clean = key.strip().lower().replace(" ", "_").replace("-", "_")
        canonical = alias_map.get(clean, key)
        if canonical in known:
            out[canonical] = value
    return out


async def validate_fields(
    fields: Dict[str, Any], spec: LeadGenSpec
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Validate each supplied field; keep valid values and collect per-field errors."""
    validated: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, value in fields.items():
        if key.startswith("_"):
            validated[key] = value
            continue
        fdef = spec.get_field(key)
        if fdef is None:
            continue
        str_val = str(value).strip() if value is not None else ""
        if not str_val:
            if fdef.decline_value is not None:
                validated[key] = fdef.decline_value
            continue
        if str_val.strip().lower() == "decline_value":
            if fdef.decline_value is not None:
                validated[key] = str(fdef.decline_value)
            else:
                errors[key] = "this field has no decline value; provide a value"
            continue
        if _is_decline_value(fdef, str_val):
            validated[key] = str(fdef.decline_value)
            continue
        if fdef.decline_value is not None and str_val == str(fdef.decline_value):
            validated[key] = str_val
            continue
        validator_name = locale_validator(fdef.phone_locale) or fdef.validator
        if fdef.phone_locale and not validator_name:
            validator_name = "phone_e164"
        if fdef.key == "phone" and not validator_name:
            validator_name = "phone_e164"
        if validator_name:
            normalized, err = run_validator(
                validator_name, str_val, fdef.validator_args
            )
            if err:
                errors[key] = err
                continue
            validated[key] = normalized
        else:
            validated[key] = str_val
    return validated, errors


def apply_merge_fields(
    fields: Dict[str, Any],
    profile_data: Dict[str, Any],
    merge_keys: List[str],
) -> Dict[str, Any]:
    for merge_field in merge_keys:
        new_val = str(fields.get(merge_field, "")).strip()
        if not new_val:
            continue
        old_val = str(profile_data.get(merge_field, "")).strip()
        if not old_val:
            continue
        old_items = [p.strip() for p in old_val.split(",") if p.strip()]
        new_items = [p.strip() for p in new_val.split(",") if p.strip()]
        merged_set = {i.lower() for i in old_items}
        merged_list = list(old_items)
        for item in new_items:
            if item.lower() not in merged_set:
                merged_list.append(item)
                merged_set.add(item.lower())
        fields[merge_field] = ", ".join(merged_list)
    return fields


async def maybe_auto_sync(
    action: "LeadGenAction",
    spec: LeadGenSpec,
    record: LeadRecord,
    profile_data: Dict[str, Any],
    user_id: str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    sync_cfg = spec.sync
    if not force and sync_cfg.mode == "manual":
        logger.debug("leadgen sync: skipped (manual mode) user=%s", user_id)
        return {"status": "skipped", "reason": "manual mode"}
    if not force and sync_cfg.mode == "on_complete":
        missing = record.get_missing_fields()
        if missing:
            logger.debug(
                "leadgen sync: skipped (required fields incomplete: %s) user=%s",
                missing,
                user_id,
            )
            return {"status": "skipped", "reason": "required fields incomplete"}
    if not force and sync_cfg.mode == "on_capture":
        if not sync_mod.sync_threshold_met(
            profile_data, sync_cfg.min_fields, sync_cfg.require_any
        ):
            logger.debug(
                "leadgen sync: skipped (sync thresholds not met) user=%s", user_id
            )
            return {"status": "skipped", "reason": "sync thresholds not met"}

    destinations = list(sync_cfg.destinations)
    if action.sync_destinations and not destinations:
        destinations = list(action.sync_destinations)

    if not destinations:
        logger.debug("leadgen sync: no destinations configured user=%s", user_id)
        return {"status": "no-op", "reason": "no destinations configured"}

    results, any_success = await sync_mod.sync_to_destinations(
        action, destinations, profile_data, user_id
    )
    if any_success:
        updated = dict(profile_data)
        updated[sync_mod.DIGEST_KEY] = sync_mod.compute_digest(profile_data)
        await record.set_yaml(updated)
    status = "sync-complete" if any_success else "no-op"
    logger.debug("leadgen sync: status=%s results=%s user=%s", status, results, user_id)
    return {"status": status, "results": results}


def _field_filled(fields: Dict[str, Any], key: str) -> bool:
    val = fields.get(key)
    if val is None:
        return False
    return bool(str(val).strip())


def _declined_keys(fields: Dict[str, Any], spec: LeadGenSpec) -> set:
    """Keys whose stored value equals their own ``decline_value``."""
    out: set = set()
    for fdef in spec.fields:
        if fdef.decline_value is None:
            continue
        val = fields.get(fdef.key)
        if val is None:
            continue
        if str(val).strip().lower() == str(fdef.decline_value).strip().lower():
            out.add(fdef.key)
    return out


def _relevant_when_met(
    fields: Dict[str, Any], fdef: Any, declined: Optional[set] = None
) -> bool:
    """True when the field has no gate, or its gate is satisfied.

    ``any_present`` needs one listed key filled. ``not_declined`` needs every
    listed key filled and none of them holding its own ``decline_value``.
    """
    gate = getattr(fdef, "relevant_when", None) or None
    if not gate:
        return True
    any_present = gate.get("any_present") or []
    if any_present and not any(
        _field_filled(fields, k) and (declined is None or k not in declined)
        for k in any_present
    ):
        return False
    for key in gate.get("not_declined") or []:
        if not _field_filled(fields, key):
            return False
        if declined is not None and key in declined:
            return False
    return True


MAX_OPTIONAL_FIELDS = 5

#: Short refusals the model may pass instead of the field's decline text. The
#: action normalises them so gating and the directive see a real decline; a
#: field without a decline_value keeps the visitor's words and never guesses.
DECLINE_PHRASES = frozenset(
    {
        "no",
        "none",
        "n/a",
        "na",
        "not provided",
        "prefer not to say",
        "prefer not",
        "i don't want to say",
        "i dont want to say",
        "dont want to say",
        "don't want to say",
        "no thanks",
        "skip",
        "no organization",
        "no organisation",
        "no company",
        "personal use",
    }
)


def _is_decline_value(fdef: Any, value: Any) -> bool:
    """True when the supplied value means a refusal for this field."""
    if fdef is None or getattr(fdef, "decline_value", None) is None:
        return False
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text == "decline_value" or text in DECLINE_PHRASES


def optional_field_keys(fields: Dict[str, Any], spec: LeadGenSpec) -> List[str]:
    """Open optional keys that pass their ``relevant_when`` gate.

    Keys only: each key's guidance stays in ``field_reference``, so the payload
    never repeats the same text twice. The list is capped at
    ``MAX_OPTIONAL_FIELDS`` so the directive stays a bounded menu.
    """
    declined = _declined_keys(fields, spec)
    out: List[str] = []
    for fdef in spec.fields:
        if fdef.required:
            continue
        if getattr(fdef, "capture_only", False):
            continue
        if _field_filled(fields, fdef.key):
            continue
        if not _relevant_when_met(fields, fdef, declined):
            continue
        out.append(fdef.key)
    return out[:MAX_OPTIONAL_FIELDS]


SYNC_COMPLETE_DIRECTIVE = (
    "The request is with the sales team. Acknowledge the captured value "
    "naturally — name it only when it adds clarity; do not thank the visitor "
    "every turn. Reply with the hand-off sentence and ask no further field "
    "this turn."
)

#: Required fields are complete but the hand-off did not run (no destination, a
#: destination error, or a denied call). The reply must say so and must never
#: claim the request was sent.
SYNC_FAILED_DIRECTIVE = (
    "The request is saved but not yet sent to the sales team. In one sentence say "
    "that, name anything still needed in the visitor's own words, and offer the "
    "sales contact +592-628-4077. Never say sent, received, forwarded, or passed "
    "on. Ask no further field."
)

# Internal, underscore-prefixed so it is excluded from the sync digest and from
# every destination template (see ``sync.compute_digest``).
HANDOFF_STATED_KEY = "_leadgen_handoff_stated"


def handoff_directive(
    optional_keys: List[str],
    *,
    already_stated: bool = False,
    just_declined: bool = False,
) -> str:
    """The hand-off reply, plus one optional when the pool is still open.

    ``already_stated`` suppresses the hand-off sentence on later captures; the
    optional ask keeps the same need criterion.
    """
    if just_declined:
        if already_stated:
            return (
                "The request is already with the sales team; do not repeat the "
                "hand-off sentence. Reply with this exact sentence — “Noted — the "
                "request stays with the sales team as it is, and anything you add "
                "later will update it.” — then ask no field; the visitor declined "
                "or signalled they do not want more questions."
            )
        return (
            "The request is with the sales team. Reply with the hand-off sentence, "
            "acknowledge naturally, and ask no field — the visitor declined or "
            "signalled they do not want more questions. When the visitor supplied "
            "an optional value this turn, say it is being added to the same "
            "request."
        )
    if not optional_keys:
        if already_stated:
            return (
                "The request is already with the sales team; do not repeat the "
                "hand-off sentence. Acknowledge in one sentence that the same "
                "request is up to date with the sales team; never reply with a "
                "bare acknowledgement; ask no field."
            )
        return SYNC_COMPLETE_DIRECTIVE
    listed = ", ".join(f"`{k}`" for k in optional_keys)
    if already_stated:
        return (
            "The request is already with the sales team; do not repeat the "
            "hand-off sentence, but do say in one sentence that this detail is "
            "added to the same request — never reply with a bare acknowledgement. "
            "Acknowledge in one sentence that this detail updates the same request. "
            f"Then ask at most one optional field from {listed} only when the "
            "visitor engaged with a value and has not refused or signalled they "
            "are done; otherwise ask no field and close. Never rotate to another "
            "optional after a refusal."
        )
    return (
        "The request is with the sales team. Acknowledge the captured value "
        "naturally; do not thank every turn. Reply with the hand-off sentence, "
        f"then ask at most one optional field from {listed} only when the visitor "
        "engaged with a value and has not refused or signalled they are done; "
        "otherwise ask no field and close. A refusal or ignore moves on; the "
        "request is already filed."
    )


def next_ask_directive(
    next_required: Optional[str],
    optional_keys: List[str],
    *,
    just_declined: bool = False,
) -> str:
    """The ask for this reply, derived from the record (not chosen by the model)."""
    if next_required:
        return (
            "Acknowledge the captured value naturally — name it only when it adds "
            "clarity; do not thank the visitor every turn and do not open every "
            "reply the same way. Then ask only for "
            f"`{next_required}` in this reply; do not ask any other field. If the "
            "visitor refuses it, or answers something else and keeps avoiding it, "
            "do not ask for it again in that turn: reply with this exact sentence "
            f"— “{next_required.replace('_', ' ')} is needed to file this request "
            "— would you like to continue or cancel?” If they continue, ask for it "
            "once more in fresh words; if they cancel, use the cancel tool. Never "
            "store a refusal as the value."
        )
    if optional_keys:
        listed = ", ".join(f"`{k}`" for k in optional_keys)
        if just_declined:
            return (
                "Acknowledge naturally in one short sentence and ask no further "
                "field. The visitor declined or signalled they do not want more "
                "questions, so do not rotate to another optional. The request is "
                "saved, and any further details they give update the same "
                "request. Do not say it was sent unless the hand-off has "
                "completed."
            )
        return (
            "Acknowledge the captured value naturally — name it only when it adds "
            "clarity; do not thank the visitor every turn. Required fields are "
            f"complete. Ask at most one optional field from {listed}, and only "
            "when the visitor answered with a value and has not refused, skipped, "
            "or signalled they are done. If the message refuses, skips, says "
            "'I don't want to say' or 'no more questions', or otherwise shows the "
            "visitor does not want more questions, acknowledge in one sentence and "
            "ask no further field. Never rotate to another optional after a "
            "refusal; optional fields never block the request."
        )
    return (
        "Acknowledge the captured value naturally only if it adds clarity; do not "
        "thank the visitor every turn. Required fields are complete and no "
        "optional field is open. Do not ask another field: answer, confirm, or "
        "close."
    )


def capture_state(
    record: Any, spec: LeadGenSpec, *, just_declined: bool = False
) -> Dict[str, Any]:
    """The record snapshot every capture/retrieve result carries.

    ``next_required`` names the single required field to ask now, and
    ``directive`` states the ask for this reply; ``optional_fields`` lists the
    open optional keys once required is complete. The model reads guidance from
    ``field_reference`` and keeps only the timing and value judgement.
    """
    profile_data = record.get_yaml() or {}
    missing = record.get_missing_fields()
    fields = {k: v for k, v in profile_data.items() if not str(k).startswith("_")}
    next_required = missing[0] if missing else None
    optional_keys = optional_field_keys(fields, spec)
    return {
        "fields": fields,
        "missing_fields": missing,
        "next_required": next_required,
        "optional_fields": optional_keys,
        "field_reference": fields_reference(spec),
        "directive": next_ask_directive(
            next_required, optional_keys, just_declined=just_declined
        ),
    }


async def handle_capture(
    action: "LeadGenAction",
    fields: Optional[Dict[str, Any]] = None,
    skill: Optional[str] = None,
    visitor: Any = None,
    **kwargs: Any,
) -> str:
    user, interaction = await get_user_and_interaction(visitor)
    if not user or not interaction:
        return json.dumps({"error": "no active interaction"})

    raw_fields: Dict[str, Any] = dict(fields or {})
    kwargs.pop("ask_next", None)
    raw_fields.pop("ask_next", None)
    raw_fields.update(
        {k: v for k, v in kwargs.items() if k not in ("visitor", "skill")}
    )

    spec = merge_spec_with_action_defaults(action, resolve_spec(action, skill))
    if not raw_fields:
        return json.dumps({"status": "no-op", "reason": "no fields provided"})

    raw_fields = canonicalize_fields(raw_fields, spec)

    dedup_key = (user.user_id, json.dumps(raw_fields, sort_keys=True, default=str))
    now = time.time()
    if now - _LAST_CAPTURE.get(dedup_key, 0) < _CAPTURE_DEDUP_TTL:
        # A repeated identical call still has to answer with the record. The
        # repeat guard nudges an identical tool call once before ending the
        # turn, so the first repeat does reach here, and a bare
        # {"status": "deduplicated"} tells a post-capture gate nothing at all —
        # the same blindness the merged snapshot exists to fix, one exit over.
        record = await LeadRecord.get_or_create_for_user(
            user, required_fields=spec.get_required_fields()
        )
        state = capture_state(record, spec)
        return json.dumps(
            {
                "status": "deduplicated",
                "fields_saved": [],
                **state,
            }
        )
    _LAST_CAPTURE[dedup_key] = now

    channel = getattr(interaction, "channel", "default") or "default"
    if channel.lower() == "whatsapp":
        if "phone" not in raw_fields and user.user_id:
            raw_fields.setdefault("phone", user.user_id)
        if not raw_fields.get("full_name") and getattr(user, "name", None):
            raw_fields.setdefault("full_name", user.name)

    validated, validation_errors = await validate_fields(raw_fields, spec)
    just_declined = bool(_declined_keys(validated, spec))

    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields()
    )
    profile_data = record.get_yaml() or {}
    changed = False
    if validated:
        ctx = HookExecutionContext(
            spec=spec,
            record=record,
            profile_data=profile_data,
            fields=validated,
            visitor=visitor,
            user=user,
        )
        if spec.handlers.post_capture:
            ctx = await call_hook(spec, spec.handlers.post_capture, ctx)
            validated = ctx.fields

        if spec.handlers.qualify:
            ctx = await call_hook(spec, spec.handlers.qualify, ctx)
            if ctx.blocked:
                return json.dumps(
                    {
                        "status": "blocked",
                        "reason": ctx.block_reason or "qualification failed",
                    }
                )

        validated = apply_merge_fields(validated, profile_data, spec.merge_fields())
        changed = await record.update_yaml(validated)

    state = capture_state(record, spec, just_declined=just_declined)
    result: Dict[str, Any] = {
        "status": (
            "updated"
            if changed
            else ("invalid-argument" if validation_errors else "no-op")
        ),
        "fields_saved": list(validated.keys()) if changed else [],
        **state,
    }
    if validation_errors:
        result["validation_errors"] = validation_errors

    if changed:
        await record.append_to_section(
            "conversation_summaries",
            "Set: "
            + "; ".join(
                f"{k} = '{str(v)[:80]}'"
                for k, v in validated.items()
                if not k.startswith("_")
            ),
        )

    profile_data = record.get_yaml() or {}
    if changed and spec.sync.mode != "manual":
        sync_result = await maybe_auto_sync(
            action, spec, record, profile_data, user.user_id
        )
        result["sync_result"] = sync_result

        if sync_result.get("status") == "sync-complete":
            # The hand-off owns this reply; one optional may ride along when
            # the pool is still open, and the pool resumes on the next capture.
            # After the first hand-off the sentence is not repeated.
            already_stated = bool(profile_data.get(HANDOFF_STATED_KEY))
            result["directive"] = handoff_directive(
                result.get("optional_fields") or [],
                already_stated=already_stated,
                just_declined=just_declined,
            )
            if not already_stated:
                profile_data[HANDOFF_STATED_KEY] = True
                await record.update_yaml({HANDOFF_STATED_KEY: True})
        elif not result.get("missing_fields"):
            # Required fields are complete but the hand-off did not run. Say it
            # is saved and not sent; never let the reply claim otherwise.
            result["directive"] = SYNC_FAILED_DIRECTIVE

        if spec.handlers.on_sync:
            ctx = HookExecutionContext(
                spec=spec,
                record=record,
                profile_data=profile_data,
                fields=validated,
                visitor=visitor,
                user=user,
                extra={"sync_result": sync_result},
            )
            await call_hook(spec, spec.handlers.on_sync, ctx)

    return json.dumps(result)


async def handle_retrieve(
    action: "LeadGenAction",
    skill: Optional[str] = None,
    visitor: Any = None,
    **_: Any,
) -> str:
    user, _ = await get_user_and_interaction(visitor)
    if not user:
        return json.dumps({"error": "no user found"})

    spec = merge_spec_with_action_defaults(action, resolve_spec(action, skill))
    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields()
    )
    profile_data = record.get_yaml() or {}
    clean = {k: v for k, v in profile_data.items() if not k.startswith("_")}
    state = capture_state(record, spec)

    return json.dumps(
        {
            "status": "ok" if clean else "empty_profile",
            **state,
            "score": record.score,
            "enrichment_status": record.enrichment_status,
        }
    )


async def handle_status(
    action: "LeadGenAction",
    skill: Optional[str] = None,
    visitor: Any = None,
    **_: Any,
) -> str:
    user, _ = await get_user_and_interaction(visitor)
    if not user:
        return json.dumps({"error": "no user found"})

    spec = merge_spec_with_action_defaults(action, resolve_spec(action, skill))
    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields()
    )
    profile_data = record.get_yaml() or {}
    digest = sync_mod.get_stored_digest(profile_data)

    return json.dumps(
        {
            "status": "ok",
            "missing_fields": record.get_missing_fields(),
            "required_fields": record.get_required_fields(),
            "score": record.score,
            "enrichment_status": record.enrichment_status,
            "last_sync_digest": digest,
            "sync_mode": spec.sync.mode,
        }
    )


async def handle_sync(
    action: "LeadGenAction",
    skill: Optional[str] = None,
    visitor: Any = None,
    **_: Any,
) -> str:
    user, _ = await get_user_and_interaction(visitor)
    if not user:
        return json.dumps({"error": "no user found"})

    spec = merge_spec_with_action_defaults(action, resolve_spec(action, skill))
    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields()
    )
    profile_data = record.get_yaml() or {}
    if not profile_data:
        return json.dumps({"status": "no-op", "reason": "profile empty"})

    sync_result = await maybe_auto_sync(
        action, spec, record, profile_data, user.user_id, force=True
    )
    return json.dumps(sync_result)
