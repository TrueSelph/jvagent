"""Visitor shim for ``SkillCatalog.discover`` / ``ToolExecutor`` (matches ``_AgentShim``)."""

from __future__ import annotations

from typing import Any, Optional

from jvagent.action.skill.action_resolver import ActionResolver


class AgentInteractVisitorShim:
    """Minimal shim exposing attributes SkillCatalog / ToolExecutor expect from a visitor."""

    def __init__(
        self,
        agent: Any,
        action_resolver: Optional[ActionResolver],
        user_id: Optional[str] = None,
        conversation: Any = None,
        interaction: Any = None,
        session_id: Optional[str] = None,
        response_bus: Any = None,
        channel: Optional[str] = None,
    ) -> None:
        self._agent = agent
        self.action_resolver = action_resolver
        self.user_id = (user_id or "").strip() or None
        self.conversation = conversation
        self.interaction = interaction
        self.session_id = session_id
        self.response_bus = response_bus
        self.channel = channel or ""
