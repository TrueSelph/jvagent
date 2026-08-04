"""Exclusions + wrapping shared by tool-surface assembly and later re-adds.

``_assemble_tools`` is not the only place tools enter a turn's surface: skill
activation can *materialize* a tool a bound action pruned while its runtime
warmed up (``ensure_skill_tools_materialized``). That late path must apply the
same exclusions the assembly applied, or it silently re-derives a surface the
configuration ruled out:

- ``denied_tools`` (ADR-0015) — "not listed, not find_tool-reachable, not
  dispatchable". A skill naming a denied tool in ``allowed-tools`` must not
  make it callable.
- ``tool_servers`` — an MCP server the selector excludes contributes nothing,
  so its tools are unreachable by any path.
- AccessControl labels — MCP tools dispatch through
  ``tool:delegate:{name}`` (ADR-0012 inv. 6); re-wrapping one without its label
  silently drops per-user gating.

:class:`ToolSurfacePolicy` is the single object carrying those rules, built once
per turn from the orchestrator's (channel-resolved) config so assembly and
materialization cannot diverge.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, Optional, Set, Tuple

from jvagent.action.orchestrator.access import delegate_resource_label
from jvagent.action.orchestrator.constants import STEER_EXEMPT
from jvagent.action.orchestrator.tools import SkillTool, wrap_action_tool


def mcp_action_names(action: Any) -> Set[str]:
    """Names a ``tool_servers`` selector may use to refer to *action*."""
    out: Set[str] = set()
    get_name = getattr(action, "get_class_name", None)
    if callable(get_name):
        try:
            out.add(get_name())
        except Exception:
            pass
    for attr in ("name", "package_name"):
        val = getattr(action, attr, None)
        if isinstance(val, str) and val:
            out.add(val)
    return out


@dataclass(frozen=True)
class ToolSurfacePolicy:
    """What a turn's tool surface excludes, and how a kept tool is wrapped.

    ``allowed_mcp_names`` is ``None`` for the ``-all`` selector (every enabled
    MCP server) and otherwise the union of the selected servers' names; an empty
    frozenset therefore means "no MCP server is selected", not "unrestricted".
    ``agent`` / ``user_id`` / ``channel`` supply the AccessControl context used
    when wrapping MCP tools.
    """

    denied_patterns: Tuple[str, ...] = ()
    allowed_mcp_names: Optional[FrozenSet[str]] = None
    mcp_action_class: Optional[type] = None
    agent: Any = None
    user_id: Optional[str] = None
    channel: str = "default"

    # -- exclusions ---------------------------------------------------------

    def denied_names(self, names: Iterable[str]) -> Set[str]:
        """Names matching any ``denied_tools`` glob (protected names included).

        Callers that need to report on protected matches subtract
        ``STEER_EXEMPT`` themselves; :meth:`is_denied` applies it.
        """
        if not self.denied_patterns:
            return set()
        candidates = list(names)
        out: Set[str] = set()
        for raw in self.denied_patterns:
            pat = str(raw).strip()
            if not pat:
                continue
            out |= {n for n in candidates if fnmatch.fnmatchcase(n, pat)}
        return out

    def is_denied(self, name: str) -> bool:
        """True if *name* is hard-excluded by ``denied_tools``."""
        if name in STEER_EXEMPT:
            return False
        return bool(self.denied_names([name]))

    def is_mcp_action(self, action: Any) -> bool:
        cls = self.mcp_action_class
        return cls is not None and isinstance(action, cls)

    def allows_action(self, action: Any) -> bool:
        """True if *action* may contribute tools to this turn's surface.

        Only MCP gateways are gated here — ``tool_servers`` selects which ones
        the orchestrator pulls from.
        """
        if not self.is_mcp_action(action):
            return True
        if self.allowed_mcp_names is None:
            return True
        return bool(mcp_action_names(action) & self.allowed_mcp_names)

    # -- wrapping -----------------------------------------------------------

    def wrap_mcp(self, tool: Any) -> SkillTool:
        """Wrap an MCP tool: AC-gated, no visitor injection.

        An MCP tool forwards its kwargs verbatim to the server, so a ``visitor``
        kwarg would be serialized (and fail); per-user routing comes from the
        turn's dispatch context.
        """
        return wrap_action_tool(
            tool,
            agent=self.agent,
            user_id=self.user_id,
            channel=self.channel,
            access_label=delegate_resource_label(getattr(tool, "name", "tool")),
        )

    def wrap_action(self, tool: Any, *, visitor: Any = None) -> SkillTool:
        """Wrap a plain capability tool (visitor bound only when requested)."""
        return wrap_action_tool(tool, visitor=visitor)

    def materialize(
        self, tool: Any, *, action: Any, visitor: Any = None
    ) -> Optional[SkillTool]:
        """Wrap *tool* for late re-add, or ``None`` when it is excluded."""
        name = getattr(tool, "name", None)
        if not name or self.is_denied(str(name)) or not self.allows_action(action):
            return None
        if self.is_mcp_action(action):
            return self.wrap_mcp(tool)
        bind = bool(getattr(action, "binds_tools_to_visitor", False))
        return self.wrap_action(tool, visitor=visitor if bind else None)


__all__ = ["ToolSurfacePolicy", "mcp_action_names"]
