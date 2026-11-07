"""Offline channel declarations; unsupported execution policy blocks cutover."""

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


def _retirable(value: Any, kind: str) -> bool:
    if kind == "boolean":
        return type(value) is bool
    if kind == "text":
        return isinstance(value, str)
    if kind == "texts":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    try:
        if kind == "integer":
            return type(value) is int and value >= 0 and math.isfinite(value)
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def migrate_channel_overrides(
    context: Mapping[str, Any],
    target: dict[str, Any],
    report: dict[str, Any],
    *,
    retire_kinds: Mapping[str, str],
) -> None:
    """Translate presentation and tightening ceilings, never infer tool grants.

    Reports expose setting names only. Instructions stay in the private candidate.
    The caller withholds the entire candidate when any channel remains blocked.
    """
    if "channel_overrides" not in context:
        return
    profiles = context["channel_overrides"]
    if not isinstance(profiles, dict):
        report["blockers"].append("channel_overrides requires exact channel mappings")
        return
    instructions: dict[str, str] = {}
    limits: dict[str, dict[str, Any]] = {}
    retired: set[str] = set()
    blocked: set[str] = set()
    for channel, declaration in profiles.items():
        if (
            not isinstance(channel, str)
            or not channel
            or channel != channel.strip()
            or not isinstance(declaration, dict)
        ):
            report["blockers"].append(
                "channel_overrides has an invalid channel declaration"
            )
            continue
        extra = ""
        length_instruction = ""
        policy: dict[str, Any] = {}
        for key, value in declaration.items():
            try:
                if key == "system_prompt_extra":
                    if not isinstance(value, str):
                        raise ValueError
                    extra = value
                elif key == "max_statement_length":
                    if not _retirable(value, "integer"):
                        raise ValueError
                    if value:
                        length_instruction = f"Keep final replies within {value} characters when practical."
                elif key in {
                    "activation_budget",
                    "max_duration_seconds",
                    "max_turn_cost_usd",
                }:
                    name = {
                        "activation_budget": "max_requests",
                        "max_duration_seconds": "timeout_seconds",
                        "max_turn_cost_usd": "max_run_cost_usd",
                    }[key]
                    if name not in target:
                        raise ValueError
                    if key == "max_turn_cost_usd":
                        if type(value) not in (str, int, float):
                            raise ValueError
                        number = Decimal(str(value))
                        if not number.is_finite() or number <= 0:
                            raise ValueError
                        encoded: Any = str(number)
                        ceiling = Decimal(target[name])
                    else:
                        if not _retirable(
                            value, "integer" if key == "activation_budget" else "number"
                        ):
                            raise ValueError
                        number = max(1, value) if key == "activation_budget" else value
                        if number <= 0:
                            raise ValueError
                        encoded = number
                        ceiling = target[name]
                    if number > ceiling:
                        raise ValueError
                    policy[name] = encoded
                elif key in retire_kinds and _retirable(value, retire_kinds[key]):
                    retired.add(key)
                elif (
                    key in {"pinned_tools", "skill_only_tools", "denied_tools"}
                    and type(value) is list
                    and not value
                ):
                    retired.add(key)
                elif (
                    key == "tool_call_timeout"
                    and type(value) in (int, float)
                    and value == 0
                ) or (key == "with_event" and value is False):
                    retired.add(key)
                else:
                    raise ValueError
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                reportable = set(retire_kinds) | {
                    "system_prompt_extra",
                    "max_statement_length",
                    "activation_budget",
                    "max_duration_seconds",
                    "max_turn_cost_usd",
                    "pinned_tools",
                    "skill_only_tools",
                    "denied_tools",
                    "tool_call_timeout",
                    "with_event",
                }
                blocked.add(key if key in reportable else "unknown")
                report["blockers"].append(
                    "channel_overrides contains unsupported, invalid or widening policy; explicit review required"
                )
        text = [extra, length_instruction]
        if any(text):
            instructions[channel] = "\n\n".join(part for part in text if part)
        if policy:
            limits[channel] = policy
    if instructions:
        target["channel_instructions"] = instructions
    if limits:
        target["channel_limits"] = limits
    report["mappings"][
        "channel_overrides"
    ] = "channel_instructions/channel_limits; explicit inactive or engine-control retirement"
    if retired:
        report["retired_channel_settings"] = sorted(retired)
    if blocked:
        report["blocked_channel_settings"] = sorted(blocked)
    report["required_review"].append(
        "Review channel instructions as model guidance, move domain procedures into skills, and qualify retired channel heuristics before cutover; channel labels grant no authority"
    )
