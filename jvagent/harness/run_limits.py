"""Resolve host workload ceilings; the public SDK enforces execution limits."""

import math
from decimal import Decimal, InvalidOperation
from typing import Any

_FIELDS = (
    "max_requests",
    "max_tool_calls",
    "max_tokens",
    "max_output_tokens",
    "timeout_seconds",
    "max_run_cost_usd",
)


def _number(
    key: str, value: Any, *, optional: bool = False
) -> int | float | Decimal | None:
    if optional and key == "max_output_tokens" and value is None:
        return None
    if key == "max_run_cost_usd":
        try:
            if not isinstance(value, str):
                raise ValueError
            cost = Decimal(value)
            if not cost.is_finite() or cost <= 0:
                raise ValueError
            return cost
        except (ValueError, InvalidOperation):
            raise ValueError(
                "Run cost ceilings require positive finite decimal text"
            ) from None
    if key == "timeout_seconds":
        valid = (type(value) is int and value > 0) or (
            type(value) is float and math.isfinite(value) and value > 0
        )
    else:
        valid = type(value) is int and value > 0
    if not valid:
        raise ValueError(
            "Run ceilings require positive finite values of the declared type"
        )
    return value


def effective_run_limits(action: Any, channel: str) -> dict[str, Any]:
    """Validate every declaration and select an exact-channel tightening only.

    Channel labels grant no authority. A caller-selected label can never raise
    the agent's base ceiling. Conversation spending remains shared and unchanged.
    No prompts, tools, providers or routing settings are admitted in this map.
    """
    base = {key: getattr(action, key) for key in _FIELDS}
    ceilings = {key: _number(key, value, optional=True) for key, value in base.items()}
    policies = getattr(action, "channel_limits", {})
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("A nonempty channel label is required")
    if not isinstance(policies, dict):
        raise ValueError("Channel limits must be a mapping")
    for name, declaration in policies.items():
        if not isinstance(name, str) or not name or name != name.strip():
            raise ValueError("Channel limit keys require exact nonempty labels")
        if not isinstance(declaration, dict) or declaration.keys() - base.keys():
            raise ValueError("Channel limits admit only native run ceiling fields")
        for key, value in declaration.items():
            limit = _number(key, value)
            if ceilings[key] is not None and limit > ceilings[key]:
                raise ValueError("Channel limits may only tighten agent ceilings")
    return {**base, **policies.get(channel, {})}
