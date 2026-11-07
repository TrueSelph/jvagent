"""Local lead capture through the native SDK mutation contract."""

import json
from typing import TYPE_CHECKING, Any

from jvspatial import flush_deferred_entities

from jvagent.harness.mutation_tools import NativeMutationTool
from jvagent.memory.lock_manager import get_user_lock_manager

from . import engine
from .store import LeadRecord

if TYPE_CHECKING:
    from .leadgen_action import LeadGenAction


class LocalLeadCaptureTool(NativeMutationTool):
    effect_summary = "jv:mutation:leadgen-local-capture:v1"


def local_capture_tool(action: "LeadGenAction") -> LocalLeadCaptureTool:
    """Expose reviewed local fields without running hooks or external sync."""
    spec = engine.merge_spec_with_action_defaults(action, None)
    keys = set(spec.field_keys())
    if (
        action._registry.specs
        or action.sync_destinations
        or not keys
        or any(not key or key.startswith("_") for key in keys)
    ):
        raise ValueError(
            "Native local capture requires explicit local fields without skill hooks or sync destinations"
        )

    async def capture(*, visitor: Any, fields: dict[str, str]) -> str:
        if (
            not fields
            or set(fields) - keys
            or any(
                not isinstance(value, str) or len(value) > 2048
                for value in fields.values()
            )
        ):
            return json.dumps(
                {
                    "status": "invalid-argument",
                    "error": "Use declared lead fields and bounded text",
                }
            )
        validated, error = await engine.validate_fields(fields, spec)
        if error:
            return json.dumps({"status": "invalid-argument", "error": error})
        user, interaction = await engine.get_user_and_interaction(visitor)
        if user is None or interaction is None:
            raise ValueError("Lead capture requires the active caller interaction")
        lock = await get_user_lock_manager().acquire(f"native-lead-capture:{user.id}")
        async with lock:
            record = await LeadRecord.get_or_create_for_user(
                user, spec.get_required_fields() or None
            )
            updates = engine.apply_merge_fields(
                validated, record.get_yaml(), spec.merge_fields()
            )
            changed = await record.update_yaml(updates)
            await flush_deferred_entities(record, strict=True)
            observed = await LeadRecord.get_for_user(user)
            if observed is None or any(
                observed.get_yaml().get(key) != value for key, value in updates.items()
            ):
                raise RuntimeError("Lead capture requires reconciliation")
        return json.dumps(
            {
                "status": "updated" if changed else "no-op",
                "fields_saved": list(updates) if changed else [],
                **engine.capture_state(observed, spec),
            }
        )

    return LocalLeadCaptureTool(
        name="leadgen__capture",
        description="Save explicitly supplied fields on the current caller's local lead profile.",
        parameters_schema={
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string", "maxLength": 2048}
                        for key in sorted(keys)
                    },
                    "additionalProperties": False,
                    "minProperties": 1,
                }
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
        execute=capture,
        effect_class="write",
        binds_visitor=True,
        requires_tool_permission=True,
    )
