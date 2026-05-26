"""Visitor shim for SkillCatalog.discover (self-contained for cockpit)."""

from __future__ import annotations

from typing import Any, Optional

from jvagent.action.helm.reasoning.catalog.action_resolver import ActionResolver


class CockpitVisitorShim:
    """Minimal shim exposing attributes SkillCatalog expects from a visitor."""

    def __init__(
        self,
        agent: Any,
        action_resolver: Optional[ActionResolver] = None,
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
