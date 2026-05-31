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
_ORCHESTRATOR_ACTIONS = frozenset(
    {"jvagent/orchestrator", "jvagent/interact_router"}
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

    orchestrators = [
        entry.get("action")
        for entry in actions
        if isinstance(entry, dict)
        and isinstance(entry.get("action"), str)
        and entry.get("action") in _ORCHESTRATOR_ACTIONS
    ]
    if len(orchestrators) > 1:
        warnings.append(
            _mk(
                "actions",
                f"Mutually exclusive orchestrators installed: {orchestrators}",
                hint=(
                    "Use either jvagent/orchestrator or jvagent/interact_router, "
                    "not both on the same agent."
                ),
            )
        )

    return warnings


def warn_agent_yaml(data: Dict[str, Any], source: str = "agent.yaml") -> None:
    """Run agent.yaml validation and emit deduplicated warnings."""
    _warn_once(validate_agent_yaml(data), source=source)


def _reset_warning_cache_for_tests() -> None:
    """Reset in-memory warning dedupe cache (tests only)."""
    _SEEN_WARNING_KEYS.clear()
