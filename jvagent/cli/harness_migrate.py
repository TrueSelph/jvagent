"""Offline, explicit configuration migration; never invokes an execution engine."""

import argparse
import copy
import hashlib
import json
import math
import os
import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from jvagent.cli.harness_migration_skills import migrate_skill_selection

_SCHEMA = "jvagent.native-config-migration.v1"
_MODEL_ACTIONS = {"OpenAILanguageModelAction", "LiteLLMLanguageModelAction"}
_MODEL_SERVICES = {
    "jvagent/openai_lm",
    "jvagent/ollama_lm",
    "jvagent/anthropic_lm",
    "jvagent/gemini_lm",
    "jvagent/litellm_lm",
    "jvagent/groq_lm",
    "jvagent/openrouter_lm",
}
_REVIEWED_CONSUMERS = {
    "jvagent/orchestrator",
    "jvagent/reply",
    "jvagent/mcp",
    "jvagent/task_monitor",
    "jvagent/interview",
    "jvagent/access_control",
    "jvagent/web_fetch",
    "jvagent/web_search",
    "jvagent/serper_web_search",
    "jvagent/pageindex_read",
}
_DIRECT = {"enabled", "weight", "description"}
_LIMITS = {
    "activation_budget": ("max_requests", 20),
    "max_duration_seconds": ("timeout_seconds", 180.0),
    "max_turn_cost_usd": ("max_run_cost_usd", "2.00"),
    "max_conversation_cost_usd": ("max_conversation_cost_usd", "20.00"),
}
# Explicitly retired engine heuristics, not host authorization or budget policy.
_RETIRE = {
    "light_model": "text",
    "light_model_action_type": "text",
    "light_model_temperature": "number",
    "light_model_max_tokens": "integer",
    "enable_transient_ack": "boolean",
    "ack_on_first_tool_call": "boolean",
    "ack_interval_ms": "integer",
    "first_emit_timeout_ms": "integer",
    "ack_statements": "texts",
    "stream_internal_progress": "boolean",
    "lean_tool_threshold": "integer",
    "lean_presurface_k": "integer",
    "history_limit": "integer",
}
_EMPTY = {
    "pinned_tools": list,
    "skill_only_tools": list,
    "channel_overrides": dict,
    "vision": bool,
    "planning": bool,
    "lock_active_flow": bool,
    "with_event": bool,
    "tool_call_timeout": float,
}


def _retire_controls(
    context: dict[str, Any], report: dict[str, Any], mode: Any
) -> None:
    retired = report.setdefault("retired_settings", {})
    for key, kind in _RETIRE.items():
        if key not in context:
            continue
        value = context[key]
        if kind == "boolean":
            valid = type(value) is bool
        elif kind == "text":
            valid = isinstance(value, str)
        elif kind == "texts":
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
        else:
            try:
                _number(value, key, integer=kind == "integer")
                valid = True
            except ValueError:
                valid = False
        if not valid:
            report["blockers"].append(f"Invalid legacy setting requires review: {key}")
            continue
        reason = (
            "Retired model gearing; use the configured native model and remeasure cost"
            if key.startswith("light_model")
            else (
                "Retired turn-count prompt window; native bounded SDK compaction replaces it"
                if key == "history_limit"
                else (
                    "Retired legacy pre-surfacing; official Skills and host tool ownership replace it"
                    if key.startswith("lean_")
                    else "Retired synthetic loop progress/acknowledgements; native SDK events remain"
                )
            )
        )
        retired[key] = reason
        report["mappings"][key] = "retired"
    for key, kind in _EMPTY.items():
        if key not in context:
            continue
        value = context[key]
        valid = (
            (type(value) in (int, float) and value == 0)
            if kind is float
            else type(value) is kind and not value
        )
        if not valid:
            report["blockers"].append(
                f"Setting requires qualified native policy migration: {key}"
            )
            continue
        retired[key] = (
            "Explicitly empty/disabled legacy control; no active policy removed"
        )
        report["mappings"][key] = "retired inactive control"
    if mode == "legacy":
        for key in (
            "pilot_skill",
            "pilot_max_model_requests",
            "pilot_max_total_tokens",
            "pilot_max_runtime_seconds",
            "pilot_max_output_tokens",
            "pilot_max_tool_calls",
        ):
            if key not in context:
                continue
            value = context[key]
            try:
                valid = (
                    isinstance(value, str)
                    if key == "pilot_skill"
                    else _number(value, key, integer=key != "pilot_max_runtime_seconds")
                    > 0
                )
            except ValueError:
                valid = False
            if not valid:
                report["blockers"].append(
                    f"Inactive pilot setting requires review: {key}"
                )
                continue
            retired[key] = (
                "Inactive pilot declaration; active legacy limits migrate separately"
            )
            report["mappings"][key] = "retired inactive pilot control"
    if retired:
        report["required_review"].append(
            "Review explicit control retirements and native single-model/context/event behavior before cutover"
        )


_KNOWN = (
    _DIRECT
    | set(_LIMITS)
    | set(_RETIRE)
    | set(_EMPTY)
    | {
        "skills",
        "skills_source",
        "max_statement_length",
        "model",
        "model_action_type",
        "model_temperature",
        "model_max_tokens",
        "system_prompt_extra",
        "skill_runtime",
        "pilot_skill",
        "pilot_max_model_requests",
        "pilot_max_total_tokens",
        "pilot_max_runtime_seconds",
        "pilot_max_output_tokens",
        "pilot_max_tool_calls",
    }
)


def _name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value)) and value not in {
        ".",
        "..",
    }


def _number(value: Any, field: str, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected a finite nonnegative number")
    if (
        (isinstance(value, float) and not math.isfinite(value))
        or value < 0
        or (integer and not isinstance(value, int))
    ):
        raise ValueError(f"{field}: expected a finite nonnegative number")
    return value


def plan_harness_migration(
    app_root: Path, agent_name: str, *, skill: str | Sequence[str] | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a redacted plan and candidate without graph or env resolution.

    Candidate is deliberately separate from the printable report: retained
    Action configuration and instructions can contain secrets.
    """
    root = app_root.resolve(strict=True)
    parts = agent_name.split("/")
    if len(parts) != 2 or not all(_name(part) for part in parts):
        raise ValueError("agent must be namespace/name")
    source = (root / "agents" / parts[0] / parts[1] / "agent.yaml").resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError("Agent configuration escapes the app root")
    with source.open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Agent configuration exceeds migration size limit")
    try:
        document = yaml.safe_load(raw)
    except (yaml.YAMLError, RecursionError, UnicodeDecodeError):
        raise ValueError("Agent configuration is invalid YAML") from None
    if not isinstance(document, dict) or document.get("agent") != agent_name:
        raise ValueError(
            "Agent configuration identity does not match the requested agent"
        )
    actions = document.get("actions")
    if not isinstance(actions, list) or any(
        not isinstance(a, dict) or not isinstance(a.get("action"), str) for a in actions
    ):
        raise ValueError("actions must be a list of declarations")
    owners = [
        a
        for a in actions
        if a.get("action") in {"jvagent/orchestrator", "jvagent/native_harness"}
    ]
    report: dict[str, Any] = {
        "schema": _SCHEMA,
        "source": str(source.relative_to(root)),
        "source_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "status": "blocked",
        "blockers": [],
        "mappings": {},
        "bounded_defaults": [],
        "preserved_action_count": max(0, len(actions) - 1),
        "scope": "Configuration candidate only; no graph/state migration or runtime qualification",
        "required_review": [
            "Skills, scripts, Action tools and channel compatibility",
            "Credentials and native provider settings",
            "Historical tasks, usage, checkpoints and uncertain effects before graph cutover",
            "Fresh isolated bootstrap and actual-provider browser rehearsal",
        ],
    }
    blockers = report["blockers"]
    if len(owners) != 1:
        blockers.append("Exactly one declared execution owner is required")
        return report, None
    owner = owners[0]
    if owner["action"] == "jvagent/native_harness":
        report["status"] = "already_native"
        return report, None
    if set(owner) - {"action", "context"}:
        blockers.append("Execution Action has unqualified declaration fields")
    context = owner.get("context", {})
    if not isinstance(context, dict) or any(
        not isinstance(k, str) or not _name(k) for k in context
    ):
        blockers.append("Execution Action context must contain ordinary named settings")
        return report, None
    for key in sorted(set(context) - _KNOWN):
        blockers.append(f"Unmapped execution setting: {key}")
    mode = context.get("skill_runtime", "legacy")
    if not isinstance(mode, str) or mode not in {"legacy", "capability_pilot"}:
        blockers.append("Unknown execution driver")
    _retire_controls(context, report, mode)
    if mode == "capability_pilot":
        for key in ("activation_budget", "max_duration_seconds"):
            if key in context:
                blockers.append(f"Mixed driver limit requires explicit review: {key}")
    skill_settings = migrate_skill_selection(
        root, source.parent, context, skill, report
    )
    model = context.get("model")
    model_action = context.get("model_action_type", "OpenAILanguageModelAction")
    if (
        not isinstance(model, str)
        or not isinstance(model_action, str)
        or model_action not in _MODEL_ACTIONS
    ):
        blockers.append("A qualified explicit OpenAI model declaration is required")
    elif model_action == "LiteLLMLanguageModelAction" and not model.startswith(
        "openai/"
    ):
        blockers.append("Only OpenAI-prefixed LiteLLM models have a qualified mapping")
    else:
        bare = model.removeprefix("openai/").removeprefix("openai:")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", bare):
            blockers.append("Model declaration requires an explicit provider migration")
    services = [a for a in actions if a.get("action") in _MODEL_SERVICES]
    selected_service = (
        "jvagent/openai_lm"
        if model_action == "OpenAILanguageModelAction"
        else "jvagent/litellm_lm"
    )
    retired_service = None
    if services:
        if (
            len(services) != 1
            or services[0].get("action") != selected_service
            or any(
                a.get("action") not in _REVIEWED_CONSUMERS | {selected_service}
                for a in actions
            )
        ):
            blockers.append(
                "Model service has unqualified callers or provider declarations; review before removal"
            )
        else:
            service = services[0]
            config = service.get("context", {})
            if (
                not isinstance(config, dict)
                or set(service) - {"action", "context"}
                or set(config) - {"enabled", "api_key", "api_base", "max_retries"}
            ):
                blockers.append(
                    "Model service settings require explicit native migration"
                )
            elif (
                config.get("enabled", True) is not True
                or config.get("api_key", "") not in ("", None, "${OPENAI_API_KEY}")
                or config.get("api_base", "")
                not in ("", None, "https://api.openai.com/v1")
                or type(config.get("max_retries", 0)) is not int
                or config.get("max_retries", 0) != 0
            ):
                blockers.append(
                    "Model credentials, endpoint, disabled service or retry policy requires explicit migration"
                )
            else:
                retired_service = service
                report["retired_actions"] = [selected_service]
                report["required_review"].append(
                    "Default model service retired; native provider uses OPENAI_API_KEY and zero transport retries"
                )
    target: dict[str, Any] = {
        k: copy.deepcopy(context[k]) for k in _DIRECT if k in context
    }
    try:
        for old, (new, default) in _LIMITS.items():
            value = context.get(old, 0)
            if "cost_usd" in old:
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise ValueError(f"{old}: invalid dollar limit")
                try:
                    amount = Decimal(str(value))
                except InvalidOperation:
                    raise ValueError(f"{old}: invalid dollar limit") from None
                if not amount.is_finite() or amount < 0:
                    raise ValueError(f"{old}: invalid dollar limit")
                target[new] = str(amount) if amount else default
                use_default = not amount
            else:
                target[new] = (
                    _number(value, old, integer=old == "activation_budget") or default
                )
                use_default = not value
                if old == "activation_budget" and old in context:
                    # The legacy loop clamps zero to one; zero never meant
                    # unlimited here. Do not widen this explicit ceiling.
                    target[new] = max(1, value)
                    use_default = False
            if use_default:
                report["bounded_defaults"].append(new)
            if old in context:
                report["mappings"][old] = new
        target["max_tokens"] = 100_000
        report["bounded_defaults"].append("max_tokens")
        if mode == "capability_pilot":
            for old, new, default in (
                ("pilot_max_model_requests", "max_requests", 32),
                ("pilot_max_total_tokens", "max_tokens", 100_000),
                ("pilot_max_runtime_seconds", "timeout_seconds", 300),
                ("pilot_max_tool_calls", "max_tool_calls", 48),
                ("pilot_max_output_tokens", "max_output_tokens", 20_000),
            ):
                value = _number(
                    context.get(old, default), old, integer=new != "timeout_seconds"
                )
                if not value:
                    raise ValueError(f"{old}: limit must be positive")
                target[new] = value
                report["mappings"][old] = new
                if new in report["bounded_defaults"]:
                    report["bounded_defaults"].remove(new)
        settings = {
            "temperature": context.get("model_temperature", 0.2),
            "max_tokens": context.get("model_max_tokens", 8192),
            "max_retries": 0,
        }
        if isinstance(settings["max_tokens"], bool):
            raise ValueError("model_max_tokens requires an integer")
        if settings["max_tokens"] == 0:
            settings["max_tokens"] = 8192
        if not context.get("model_max_tokens"):
            report["bounded_defaults"].append("model_settings.max_tokens")
        from jvagent.harness.model_settings import validate_model_settings

        target["model_settings"] = validate_model_settings(settings)
        if settings["max_tokens"] <= 0:
            raise ValueError("model_max_tokens must be positive")
    except (ValueError, TypeError, ImportError):
        blockers.append(
            "Model settings or limits require valid finite native values and the native SDK extra"
        )
    if blockers:
        return report, None
    target.update(model="openai:" + bare, **skill_settings)
    identity = document.get("context", {})
    if not isinstance(identity, dict):
        blockers.append("Agent identity context must be a mapping")
        return report, None
    instructions = []
    for key, label in (("alias", "Agent identity"), ("role", "Role")):
        if key in identity:
            if not isinstance(identity[key], str):
                blockers.append("Agent identity instructions require literal text")
                return report, None
            instructions.append(f"{label}: {identity[key]}")
    extra = context.get("system_prompt_extra", "")
    if not isinstance(extra, str):
        blockers.append("system_prompt_extra requires literal text")
        return report, None
    instructions.append(extra or "Help the user using the available skill.")
    if "max_statement_length" in context:
        try:
            length = _number(
                context["max_statement_length"], "max_statement_length", integer=True
            )
        except ValueError:
            blockers.append(
                "max_statement_length requires a finite nonnegative integer"
            )
            return report, None
        if length:
            instructions.append(
                f"Keep final replies within {length} characters when practical."
            )
        report["mappings"][
            "max_statement_length"
        ] = "instructions; no response truncation"
    target["instructions"] = "\n\n".join(instructions)
    report["mappings"].update(
        model="model",
        model_action_type="native provider",
        system_prompt_extra="instructions",
        model_temperature="model_settings.temperature",
        model_max_tokens="model_settings.max_tokens",
    )
    report["mappings"].update({key: key for key in _DIRECT if key in context})
    report["mappings"].update(
        {key: "native execution owner" for key in ("skill_runtime",) if key in context}
    )
    candidate = copy.deepcopy(document)
    candidate["actions"][actions.index(owner)] = {
        "action": "jvagent/native_harness",
        "context": target,
    }
    if retired_service is not None:
        candidate["actions"] = [
            a for a in candidate["actions"] if a.get("action") != selected_service
        ]
        report["preserved_action_count"] -= 1
    report["status"] = "candidate"
    return report, candidate


def handle_harness_migrate(args: list[str], app_root: str) -> int:
    """Print a safe dry run; write a new private candidate only when requested."""
    parser = argparse.ArgumentParser(prog="jvagent harness-migrate")
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--skill",
        action="append",
        help="Explicit installed skill; repeat for a finite catalog",
    )
    parser.add_argument("--output", help="New candidate YAML file; never overwrites")
    parser.add_argument(
        "--state-snapshot",
        help="Private retained-state JSON snapshot; offline preflight only",
    )
    options = parser.parse_args(args)
    try:
        report, candidate = plan_harness_migration(
            Path(app_root), options.agent, skill=options.skill
        )
        if options.state_snapshot:
            from jvagent.cli.harness_migration_state import inspect_state_snapshot

            state = inspect_state_snapshot(Path(options.state_snapshot))
            report["state_preflight"] = state
            if state["blockers"]:
                report["blockers"].append(
                    "Retained state requires reconciliation before configuration cutover"
                )
                report["status"] = "blocked"
                candidate = None
        if options.output and candidate is not None:
            output = Path(options.output).expanduser().absolute()
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    yaml.safe_dump(candidate, stream, sort_keys=False)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                output.unlink(missing_ok=True)
                raise
            report["candidate_written"] = True
        print(json.dumps(report, indent=2))
        return 2 if report["status"] == "blocked" else 0
    except (OSError, ValueError):
        print(
            json.dumps(
                {
                    "schema": _SCHEMA,
                    "status": "blocked",
                    "blockers": [
                        "Unable to plan or create a new candidate; verify paths, identity, YAML and output availability"
                    ],
                }
            )
        )
        return 2
