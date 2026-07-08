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
) -> tuple[Dict[str, Any], Optional[str]]:
    validated: Dict[str, Any] = {}
    for key, value in fields.items():
        if key.startswith("_"):
            validated[key] = value
            continue
        fdef = spec.get_field(key)
        if fdef is None:
            validated[key] = value
            continue
        str_val = str(value).strip() if value is not None else ""
        if not str_val and fdef.decline_value is not None:
            validated[key] = fdef.decline_value
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
                return {}, err
            validated[key] = normalized
        else:
            validated[key] = str_val
    return validated, None


def next_ask(spec: LeadGenSpec, missing_fields: List[str]) -> Optional[str]:
    """The single next contact field to ask for, in gap-fill priority order.

    Returns the highest-priority still-missing field so the model has an explicit
    target for the standing gap-fill ask, or ``None`` when nothing is missing.
    """
    if not missing_fields:
        return None
    missing = set(missing_fields)
    for key in spec.gap_fill.priority:
        if key in missing:
            return key
    return missing_fields[0]


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
        return {"status": "skipped", "reason": "manual mode"}
    if not force and sync_cfg.mode == "on_complete":
        if record.get_missing_fields():
            return {"status": "skipped", "reason": "required fields incomplete"}
    if not force and sync_cfg.mode == "on_capture":
        if not sync_mod.sync_threshold_met(
            profile_data, sync_cfg.min_fields, sync_cfg.require_any
        ):
            return {"status": "skipped", "reason": "sync thresholds not met"}

    destinations = list(sync_cfg.destinations)
    if action.sync_destinations and not destinations:
        destinations = list(action.sync_destinations)

    if not destinations:
        return {"status": "no-op", "reason": "no destinations configured"}

    results, any_success = await sync_mod.sync_to_destinations(
        action, destinations, profile_data, user_id
    )
    if any_success:
        updated = dict(profile_data)
        updated[sync_mod.DIGEST_KEY] = sync_mod.compute_digest(profile_data)
        await record.set_yaml(updated)
    return {"status": "sync-complete" if any_success else "no-op", "results": results}


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
    raw_fields.update(
        {k: v for k, v in kwargs.items() if k not in ("visitor", "skill")}
    )
    if not raw_fields:
        return json.dumps({"status": "no-op", "reason": "no fields provided"})

    spec = merge_spec_with_action_defaults(action, resolve_spec(action, skill))
    raw_fields = canonicalize_fields(raw_fields, spec)

    dedup_key = (user.user_id, json.dumps(raw_fields, sort_keys=True, default=str))
    now = time.time()
    if now - _LAST_CAPTURE.get(dedup_key, 0) < _CAPTURE_DEDUP_TTL:
        return json.dumps({"status": "deduplicated"})
    _LAST_CAPTURE[dedup_key] = now

    channel = getattr(interaction, "channel", "default") or "default"
    if channel.lower() == "whatsapp":
        if "phone" not in raw_fields and user.user_id:
            raw_fields.setdefault("phone", user.user_id)
        if not raw_fields.get("name") and getattr(user, "name", None):
            raw_fields.setdefault("name", user.name)

    validated, err = await validate_fields(raw_fields, spec)
    if err:
        return json.dumps({"error": err, "status": "invalid-argument"})

    record = await LeadRecord.get_or_create_for_user(
        user, required_fields=spec.get_required_fields() or None
    )
    profile_data = record.get_yaml() or {}

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

    missing_now = record.get_missing_fields()
    result: Dict[str, Any] = {
        "status": "updated" if changed else "no-op",
        "fields_saved": list(validated.keys()) if changed else [],
        "missing_fields": missing_now,
        "field_reference": fields_reference(spec),
        "gap_fill_priority": spec.gap_fill.priority,
        "next_ask": next_ask(spec, missing_now),
    }

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

    missing = record.get_missing_fields()
    return json.dumps(
        {
            "status": "ok" if clean else "empty_profile",
            "fields": clean,
            "missing_fields": missing,
            "field_reference": fields_reference(spec),
            "gap_fill_priority": spec.gap_fill.priority,
            "next_ask": next_ask(spec, missing),
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
