"""Shared fixtures for Orchestrator (ADR-0012) tests.

Construct a ``OrchestratorInteractAction`` without booting an agent graph.
``get_agent``, ``get_action``, ``publish``, the enabled-action surface, and the
model call (``_run_model``) are monkeypatched so each test drives the real
control loop + continuation with canned model decisions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.fixture
def publish_log() -> List[Dict[str, Any]]:
    return []


@pytest.fixture
def make_orchestrator(monkeypatch, publish_log):
    def _factory(
        *,
        actions: Optional[List[Any]] = None,
        action_registry: Optional[Dict[str, Any]] = None,
        decisions: Optional[List[Dict[str, Any]]] = None,
        agent: Any = None,
        activation_budget: Optional[int] = None,
    ) -> OrchestratorInteractAction:
        ex = OrchestratorInteractAction()
        if activation_budget is not None:
            ex.activation_budget = activation_budget

        if agent is None:
            agent = MagicMock()
            agent.get_access_control_action = AsyncMock(return_value=None)

        async def _get_agent(self):
            return agent

        monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _get_agent)

        reg = dict(action_registry or {})
        for a in actions or []:
            cls_name = (
                a.get_class_name()
                if callable(getattr(a, "get_class_name", None))
                else type(a).__name__
            )
            reg[cls_name] = a

        async def _get_action(self, name):
            # Mirror real get_action: accept a class or a class-name string.
            key = (
                name if isinstance(name, str) else getattr(name, "__name__", str(name))
            )
            return reg.get(key)

        monkeypatch.setattr(OrchestratorInteractAction, "get_action", _get_action)

        async def _enabled(self, _agent):
            return list(actions or [])

        monkeypatch.setattr(OrchestratorInteractAction, "_enabled_actions", _enabled)

        def _no_skills(self, _agent):
            return []

        monkeypatch.setattr(OrchestratorInteractAction, "_discover_skills", _no_skills)

        async def _publish(self, *, visitor, content, **kwargs):
            publish_log.append({"content": content, **kwargs})
            interaction = getattr(visitor, "interaction", None)
            if interaction is not None:
                interaction.response = (interaction.response or "") + content
            return None

        monkeypatch.setattr(OrchestratorInteractAction, "publish", _publish)

        seq = list(decisions or [])

        async def _run_model(
            self,
            visitor,
            utterance,
            history,
            tools,
            observations,
            flow_note="",
            skills_section="",
            finalize=False,
            gear="heavy",
            lean=False,
        ):
            return seq.pop(0) if seq else {"action": "final", "answer": ""}

        monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _run_model)

        return ex

    return _factory


class FlowStub:
    """A turn-spanning flow stub that mirrors ``InteractAction.get_tools()``.

    Exposes itself as a tool (description + anchors) whose call forwards to
    ``execute(visitor)`` — exactly the contract the Orchestrator consumes.
    Subclasses set ``anchors`` and implement ``execute``.
    """

    anchors: list = []
    description: str = ""
    always_execute: bool = False

    def get_class_name(self) -> str:
        return type(self).__name__

    async def get_anchors(self):
        return None

    async def get_tools(self):
        from jvagent.tooling.tool import Tool

        anchors = list(self.anchors or [])
        if not anchors:
            return []
        desc = (self.description or "").strip()
        desc = (
            (desc + " ").strip() + "Use when the user wants to: " + "; ".join(anchors)
        )
        return [
            Tool(
                name=self.get_class_name(),
                description=desc,
                parameters_schema={"type": "object", "properties": {}},
                execute=self._run_as_orchestrator_tool,
            )
        ]

    async def _run_as_orchestrator_tool(self, visitor=None, **kwargs):
        from jvagent.tooling.tool_result import ToolResult

        await self.execute(visitor)
        return ToolResult(content=f"(ran {self.get_class_name()})")

    async def execute(self, visitor):  # pragma: no cover - overridden
        raise NotImplementedError


@pytest.fixture
def flow_stub_cls():
    """The :class:`FlowStub` base (subclass it in tests for a flow IA)."""
    return FlowStub


@pytest.fixture
def make_visitor():
    def _factory(*, utterance: str = "hello", user_id: str = "u", channel: str = "web"):
        interaction = MagicMock()
        interaction.id = "int_1"
        interaction.utterance = utterance
        interaction.response = ""
        conversation = MagicMock()
        conversation.context = {}
        conversation.tasks = []
        conversation.save = AsyncMock()
        conversation.get_interaction_history = AsyncMock(return_value=[])
        visitor = MagicMock()
        visitor.user_id = user_id
        visitor.channel = channel
        visitor.utterance = utterance
        visitor.interaction = interaction
        visitor.conversation = conversation
        visitor.add_directives = AsyncMock()
        visitor.curate_walk_path = AsyncMock()
        return visitor

    return _factory
