"""Response policy selection across the native and transitional owners."""

from typing import Any

from jvagent.action.parameters import reply_core_parameters


def _bound_native_run(interaction: Any) -> bool:
    """Use persisted host scope, never a request/message metadata bypass flag."""
    run = getattr(interaction, "native_run", None)
    state = getattr(interaction, "native_steps", None)
    if (
        not isinstance(run, dict)
        or not isinstance(run.get("run_id"), str)
        or not run["run_id"]
    ):
        return False
    if not isinstance(state, dict) or not isinstance(state.get("runs"), list):
        return False
    expected = {
        "jv_interaction_id": getattr(interaction, "id", None),
        "jv_user_id": getattr(interaction, "user_id", None),
        "jv_session_id": getattr(interaction, "session_id", None),
        "jv_conversation_id": getattr(interaction, "conversation_id", None),
    }
    if not all(isinstance(value, str) and value for value in expected.values()):
        return False
    registrations = [
        record
        for record in state["runs"]
        if isinstance(record, dict) and record.get("run_id") == run["run_id"]
    ]
    if len(registrations) != 1:
        return False
    record = registrations[0]
    metadata = record.get("metadata")
    return bool(
        record.get("conversation_id") == expected["jv_session_id"]
        and isinstance(record.get("registration_id"), str)
        and record["registration_id"]
        and isinstance(metadata, dict)
        and isinstance(metadata.get("jv_agent_id"), str)
        and metadata["jv_agent_id"]
        and all(metadata.get(key) == value for key, value in expected.items())
    )


def egress_parameters(interaction: Any) -> list[Any] | None:
    """Native replies use declared rules; legacy replies retain their core floor.

    Remove the legacy fallback and owner distinction when retained-state migration
    and old execution removal are complete. This never changes access or delivery.
    """
    pool = getattr(interaction, "parameters", None) if interaction is not None else None
    if _bound_native_run(interaction):
        if pool is None:
            return []
        if not isinstance(pool, list) or any(
            not isinstance(rule, dict) for rule in pool
        ):
            raise ValueError("Invalid native response policy")
        return list(pool)
    try:
        listed = list(pool) if pool else []
    except TypeError:
        listed = []
    return listed + reply_core_parameters() if listed else None
