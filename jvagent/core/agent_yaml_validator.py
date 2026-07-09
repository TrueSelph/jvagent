"""Validation helpers for agent.yaml structure and expected keys."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set

from jvagent.core.yaml_validation_utils import expect_type as expect_type_generic
from jvagent.core.yaml_validation_utils import warn_once as warn_once_generic
from jvagent.core.yaml_validation_utils import (
    warn_unknown_keys as warn_unknown_keys_generic,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentYamlWarning:
    """Single agent.yaml validation warning."""

    path: str
    message: str
    hint: str = ""


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


def _warn_once(warnings: Iterable[AgentYamlWarning], source: str) -> None:
    warn_once_generic(
        warnings=warnings,
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
