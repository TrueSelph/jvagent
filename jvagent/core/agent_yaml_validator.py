"""Validation helpers for agent.yaml structure and expected keys."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set

from jvagent.core.channel import (
    CHANNEL_PROVIDERS,
    COVERAGE_SENSITIVE_OVERRIDE_KEYS,
    channel_siblings,
)
from jvagent.core.yaml_validation_utils import expect_type as expect_type_generic
from jvagent.core.yaml_validation_utils import warn_once as warn_once_generic
from jvagent.core.yaml_validation_utils import (
    warn_unknown_keys as warn_unknown_keys_generic,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentYamlWarning:
    """Single agent.yaml validation finding.

    ``severity`` is ``"warning"`` (structural problems — these fail
    ``jvagent validate``) or ``"advisory"`` (a likely-but-not-certain
    misconfiguration, printed without affecting the exit code unless
    ``--strict``). Advisories exist so a heuristic lint can ship without
    breaking the CI of apps whose config is deliberate.
    """

    path: str
    message: str
    hint: str = ""
    severity: str = "warning"


_SEEN_WARNING_KEYS: Set[str] = set()

_ALLOWED_TOP_LEVEL = {"agent", "version", "author", "jvagent", "context", "actions"}
_ALLOWED_ACTION_ENTRY_KEYS = {"action", "context", "config"}
_REMOVED_ACTION_REFS = frozenset(
    {
        "jvagent/interact_router",
        "jvagent/converse_interact_action",
        "jvagent/retrieval_interact_action",
        "jvagent/web_search_retrieval_interact_action",
        "jvagent/long_memory_retrieval_interact_action",
        "jvagent/pageindex_retrieval_interact_action",
        "jvagent/long_memory_interact_action",
        "jvagent/long_memory_store_interact_action",
    }
)


def _mk(path: str, message: str, hint: str = "") -> AgentYamlWarning:
    return AgentYamlWarning(path=path, message=message, hint=hint)


def _mk_advisory(path: str, message: str, hint: str = "") -> AgentYamlWarning:
    return AgentYamlWarning(path=path, message=message, hint=hint, severity="advisory")


def _action_entry_enabled(entry: Dict[str, Any]) -> bool:
    """False only when the entry explicitly sets ``context.enabled: false``.

    Tolerant of a malformed ``context`` (a separate warning covers that) — this
    helper must never raise, since it runs during validation of arbitrary YAML.
    """
    context = entry.get("context")
    if not isinstance(context, dict):
        return True
    return context.get("enabled") is not False


def _sets_override_key(cfg: Any, key: str) -> bool:
    """True if a per-channel override block actually overrides *key*.

    Mirrors ``_channel_cfg``'s resolution exactly, including the ``is not None``
    test: a YAML null (``skill_only_tools:`` with its entries commented out) does
    NOT override — the action-level value still applies. Treating "key present"
    as "overridden" would make this lint miss precisely the silent fallback it
    exists to catch.
    """
    return isinstance(cfg, dict) and key in cfg and cfg[key] is not None


def _check_channel_override_coverage(
    warnings: List[AgentYamlWarning],
    path: str,
    context: Any,
    enabled_action_refs: Set[str],
) -> None:
    """Advise when a channel knob is set for one sibling channel but not another.

    ``channel_overrides`` is resolved by the EXACT ``visitor.channel`` string, so
    a block written for ``whatsapp`` does nothing on a ``whatsapp_call`` (voice)
    turn — the action-level value applies instead, silently. Both keys are valid,
    so this is not a typo any key-validity check could catch; the only signal is
    that one member of a channel family is configured and its reachable sibling
    is not.

    Deliberately conservative: it fires only when the sibling channel is actually
    reachable (its providing action is enabled on this agent), and only for keys
    whose absence changes behavior silently. Anything else would be noise, and a
    lint operators learn to ignore is worse than no lint.
    """
    if not isinstance(context, dict):
        return
    overrides = context.get("channel_overrides")
    if not isinstance(overrides, dict):
        return

    for channel, cfg in overrides.items():
        if not isinstance(cfg, dict):
            continue
        channel_name = str(channel)
        for sibling in sorted(channel_siblings(channel_name)):
            provider = CHANNEL_PROVIDERS.get(sibling)
            if not provider or provider not in enabled_action_refs:
                continue  # not reachable here — saying anything would be noise
            for key in COVERAGE_SENSITIVE_OVERRIDE_KEYS:
                if not _sets_override_key(cfg, key):
                    continue
                if _sets_override_key(overrides.get(sibling), key):
                    continue
                warnings.append(
                    _mk_advisory(
                        f"{path}.context.channel_overrides",
                        (
                            f"'{key}' is overridden for channel "
                            f"'{channel_name}' but not for its sibling "
                            f"'{sibling}', which is reachable on this agent "
                            f"(provided by {provider}). Turns on "
                            f"'{sibling}' will use the action-level "
                            f"'{key}' instead."
                        ),
                        hint=(
                            f"channel_overrides is matched on the exact channel "
                            f"string. Add a '{sibling}' block setting '{key}' if "
                            f"that is not intended."
                        ),
                    )
                )


def _warn_once(warnings: Iterable[AgentYamlWarning], source: str) -> None:
    """Emit structural warnings on the runtime path — advisories are excluded.

    Advisories are a config-time lint surfaced by ``jvagent validate``. Logging
    them here too would put an unlabelled "validation warning" in the server's
    boot output on every start for a config the operator chose deliberately,
    which is exactly the runtime-warning behavior this feature set out not to
    have.
    """
    warn_once_generic(
        warnings=[w for w in warnings if w.severity != "advisory"],
        source=source,
        seen_keys=_SEEN_WARNING_KEYS,
        emit=lambda msg: logger.warning("agent.yaml validation warning %s", msg),
    )


def _expect_type(
    warnings: List[AgentYamlWarning], path: str, value: Any, expected: tuple[type, ...]
) -> None:
    expect_type_generic(
        warnings=warnings,
        path=path,
        value=value,
        types=expected,
        factory=lambda p, m, h: _mk(p, m, hint=h),
    )


def _warn_unknown_keys(
    warnings: List[AgentYamlWarning],
    base_path: str,
    payload: Dict[str, Any],
    allowed_keys: Set[str],
) -> None:
    warn_unknown_keys_generic(
        warnings=warnings,
        base_path=base_path,
        payload=payload,
        allowed_keys=allowed_keys,
        factory=lambda p, m, h: _mk(p, m, hint=h),
    )


def validate_agent_yaml(data: Dict[str, Any]) -> List[AgentYamlWarning]:
    """Validate agent.yaml payload and return warning entries."""
    warnings: List[AgentYamlWarning] = []
    if not isinstance(data, dict):
        return [_mk("agent.yaml", f"Expected mapping, got {type(data).__name__}")]

    _warn_unknown_keys(warnings, "", data, _ALLOWED_TOP_LEVEL)

    _expect_type(warnings, "agent", data.get("agent"), (str,))
    _expect_type(warnings, "version", data.get("version"), (str,))
    _expect_type(warnings, "author", data.get("author"), (str,))
    _expect_type(warnings, "jvagent", data.get("jvagent"), (str,))
    _expect_type(warnings, "context", data.get("context"), (dict,))

    actions = data.get("actions")
    if actions is None:
        return warnings
    if not isinstance(actions, list):
        warnings.append(_mk("actions", f"Expected list, got {type(actions).__name__}"))
        return warnings

    # Which action references this agent enables — needed before the per-entry
    # pass, since a channel's reachability depends on a *different* entry than
    # the one carrying the override block. ``context.enabled: false`` is honored
    # at load time, so a disabled adapter's channel is genuinely unreachable and
    # must not count.
    enabled_action_refs: Set[str] = {
        str(entry.get("action"))
        for entry in actions
        if isinstance(entry, dict)
        and isinstance(entry.get("action"), str)
        and _action_entry_enabled(entry)
    }

    orchestrator_count = 0
    for idx, action_entry in enumerate(actions):
        path = f"actions[{idx}]"
        if not isinstance(action_entry, dict):
            warnings.append(
                _mk(path, f"Expected mapping entry, got {type(action_entry).__name__}")
            )
            continue

        _warn_unknown_keys(warnings, path, action_entry, _ALLOWED_ACTION_ENTRY_KEYS)

        action_ref = action_entry.get("action")
        if not isinstance(action_ref, str):
            warnings.append(_mk(f"{path}.action", "Expected string"))
        elif "/" not in action_ref:
            warnings.append(
                _mk(
                    f"{path}.action",
                    "Expected namespace/action_name format",
                )
            )
        elif action_ref in _REMOVED_ACTION_REFS:
            warnings.append(
                _mk(
                    f"{path}.action",
                    f"Removed in jvagent 0.1.1: {action_ref}",
                    hint=(
                        "Use jvagent/orchestrator and tool-based actions "
                        "(pageindex, skills, MCP) instead of Rails-era IAs."
                    ),
                )
            )
        elif action_ref == "jvagent/orchestrator":
            orchestrator_count += 1

        _expect_type(warnings, f"{path}.context", action_entry.get("context"), (dict,))
        _expect_type(warnings, f"{path}.config", action_entry.get("config"), (dict,))
        _check_channel_override_coverage(
            warnings, path, action_entry.get("context"), enabled_action_refs
        )

    if orchestrator_count > 1:
        warnings.append(
            _mk(
                "actions",
                f"Multiple orchestrators installed ({orchestrator_count})",
                hint="Install at most one jvagent/orchestrator action per agent.",
            )
        )

    return warnings


def warn_agent_yaml(data: Dict[str, Any], source: str = "agent.yaml") -> None:
    """Run agent.yaml validation and emit deduplicated warnings."""
    _warn_once(validate_agent_yaml(data), source=source)


def _reset_warning_cache_for_tests() -> None:
    """Reset in-memory warning dedupe cache (tests only)."""
    _SEEN_WARNING_KEYS.clear()
