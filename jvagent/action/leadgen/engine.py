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
from .validators import run_validator

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
        canonical = alias_map.get(clean, key if key in known else clean)
        if canonical in known or canonical == key:
            out[canonical] = value
        else:
            out[key] = value
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
            validated[key] = value
            continue
        str_val = str(value).strip() if value is not None else ""
        if not str_val:
            if fdef.decline_value is not None:
                validated[key] = fdef.decline_value
            continue
        if fdef.decline_value is not None and str_val == str(fdef.decline_value):
            validated[key] = str_val
            continue
        validator_name = fdef.validator
        if fdef.key == "phone" and fdef.phone_locale == "GY":
            validator_name = "phone_gy"
        elif fdef.key == "phone" and not validator_name:
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


def _field_filled(fields: Dict[str, Any], key: str) -> bool:
    val = fields.get(key)
    if val is None:
        return False
    return bool(str(val).strip())


def _relevant_when_met(fields: Dict[str, Any], fdef: Any) -> bool:
    """True when the field has no gate, or any listed key is already filled."""
    gate = getattr(fdef, "relevant_when", None) or None
    if not gate:
        return True
    any_present = gate.get("any_present") or []
    if not any_present:
        return True
    return any(_field_filled(fields, k) for k in any_present)


def open_fields(
    record_fields: Dict[str, Any], spec: LeadGenSpec
) -> List[Dict[str, Any]]:
    """Empty declared fields that pass ``relevant_when`` (required + optional)."""
    out: List[Dict[str, Any]] = []
    for fdef in spec.fields:
        if _field_filled(record_fields, fdef.key):
            continue
        if not _relevant_when_met(record_fields, fdef):
            continue
        entry: Dict[str, Any] = {
            "key": fdef.key,
            "guidance": fdef.guidance,
            "required": fdef.required,
        }
        if fdef.relevant_when:
            entry["relevant_when"] = dict(fdef.relevant_when)
        out.append(entry)
    return out


def optional_fields(
    record_fields: Dict[str, Any], spec: LeadGenSpec
) -> List[Dict[str, Any]]:
    """Empty optional fields that pass ``relevant_when``.

    This is the LLM's optional-question pool: it chooses one only when the
    field's guidance and the conversation make it relevant now.
    """
    out: List[Dict[str, Any]] = []
    for fdef in spec.fields:
        if fdef.required:
            continue
        if _field_filled(record_fields, fdef.key):
            continue
        if not _relevant_when_met(record_fields, fdef):
            continue
        entry: Dict[str, Any] = {
            "key": fdef.key,
            "guidance": fdef.guidance,
            "required": False,
        }
        if fdef.relevant_when:
            entry["relevant_when"] = dict(fdef.relevant_when)
        out.append(entry)
    return out


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


def capture_state(record: Any, spec: LeadGenSpec) -> Dict[str, Any]:
    """The record snapshot every capture/retrieve result carries.

    Same keys and the same ``_`` filter as :func:`handle_retrieve`, so a caller
    reads one set no matter which tool produced the payload. There is no
    server-picked next-field pointer: the model chooses from
    ``missing_fields`` (required) or ``optional_fields`` (optional).
    """
    profile_data = record.get_yaml() or {}
    missing = record.get_missing_fields()
    fields = {k: v for k, v in profile_data.items() if not str(k).startswith("_")}
    return {
        "fields": fields,
        "missing_fields": missing,
        "optional_fields": optional_fields(fields, spec),
        "field_reference": fields_reference(spec),
        "gap_fill_priority": spec.gap_fill.priority,
        "open_fields": open_fields(fields, spec),
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
            user, required_fields=spec.get_required_fields() or None
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

    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields() or None
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

    state = capture_state(record, spec)
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
        user, required_fields=spec.get_required_fields() or None
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
        user, required_fields=spec.get_required_fields() or None
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
        user, required_fields=spec.get_required_fields() or None
    )
    profile_data = record.get_yaml() or {}
    if not profile_data:
        return json.dumps({"status": "no-op", "reason": "profile empty"})

    sync_result = await maybe_auto_sync(
        action, spec, record, profile_data, user.user_id, force=True
    )
    return json.dumps(sync_result)
