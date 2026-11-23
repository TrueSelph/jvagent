"""Compatibility imports for shared host Action authorization."""

from jvagent.tooling.access import _resolve_access_control as _resolve_access_control
from jvagent.tooling.access import (
    delegate_resource_label,
    denied_named_tool_names,
    drop_unpermitted_tools,
    is_named_tool_allowed,
    is_tool_allowed,
    named_tool_rule_applies,
)

__all__ = [
    "_resolve_access_control",
    "delegate_resource_label",
    "denied_named_tool_names",
    "drop_unpermitted_tools",
    "is_named_tool_allowed",
    "is_tool_allowed",
    "named_tool_rule_applies",
]
