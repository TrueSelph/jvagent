"""Validation helpers for agent.yaml structure and expected keys."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set

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


def _mk(path: str, message: str, hint: str = "") -> AgentYamlWarning:
    return AgentYamlWarning(path=path, message=message, hint=hint)


def _warn_once(warnings: Iterable[AgentYamlWarning], source: str) -> None:
    for w in warnings:
        key = f"{source}|{w.path}|{w.message}|{w.hint}"
        if key in _SEEN_WARNING_KEYS:
            continue
        _SEEN_WARNING_KEYS.add(key)
        suffix = f" Hint: {w.hint}" if w.hint else ""
        logger.warning(
            "agent.yaml validation warning [%s]: %s.%s", w.path, w.message, suffix
        )


def _expect_type(
    warnings: List[AgentYamlWarning], path: str, value: Any, expected: tuple[type, ...]
) -> None:
    if value is None:
        return
    if not isinstance(value, expected):
        names = "/".join(t.__name__ for t in expected)
        warnings.append(_mk(path, f"Expected {names}, got {type(value).__name__}"))


def _warn_unknown_keys(
    warnings: List[AgentYamlWarning],
    base_path: str,
    payload: Dict[str, Any],
    allowed_keys: Set[str],
) -> None:
    for key in payload.keys():
        if key not in allowed_keys:
            full_path = f"{base_path}.{key}" if base_path else key
            warnings.append(_mk(full_path, "Unexpected key"))


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

        _expect_type(warnings, f"{path}.context", action_entry.get("context"), (dict,))
        _expect_type(warnings, f"{path}.config", action_entry.get("config"), (dict,))

    return warnings


def warn_agent_yaml(data: Dict[str, Any], source: str = "agent.yaml") -> None:
    """Run agent.yaml validation and emit deduplicated warnings."""
    _warn_once(validate_agent_yaml(data), source=source)


def _reset_warning_cache_for_tests() -> None:
    """Reset in-memory warning dedupe cache (tests only)."""
    _SEEN_WARNING_KEYS.clear()
