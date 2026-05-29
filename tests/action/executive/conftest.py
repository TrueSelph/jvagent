"""Shared fixtures for Executive (ADR-0010) unit tests.

Construct an ``ExecutiveInteractAction`` and a fake walker without booting an
agent graph. ``_lookup_center``, ``get_agent``, ``get_action``, ``publish`` and
the Executive's cognition (``_executive_tick``) are monkeypatched so each test
exercises only the control loop and verb dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.contracts import YIELD
from jvagent.action.executive.executive_interact_action import ExecutiveInteractAction
from jvagent.action.executive.stub_center import StubCenter


@pytest.fixture
def publish_log() -> List[Dict[str, Any]]:
    return []


@pytest.fixture
def make_executive(monkeypatch, publish_log):
    """Factory producing a configured ``ExecutiveInteractAction``.

    Args:
        centers: dict of center name → ``BaseCenter`` instance.
        center_names: ordered names for ``executive.centers`` (defaults to keys).
        executive_script: list of ExecutiveDirectives the cognition returns in
            order. When exhausted, cognition returns ``YIELD()``.
        agent: fake agent (defaults to a MagicMock with no enforcing AC).
        activation_budget / denied_text / enable_transient_ack: overrides.
    """

    def _factory(
        *,
        centers: Optional[Dict[str, BaseCenter]] = None,
        center_names: Optional[List[str]] = None,
        executive_script: Optional[List[Any]] = None,
        agent: Any = None,
        activation_budget: Optional[int] = None,
        denied_text: Optional[str] = None,
        enable_transient_ack: Optional[bool] = None,
        registry: Any = None,
        router_responses: Optional[List[str]] = None,
    ) -> ExecutiveInteractAction:
        centers = dict(centers or {})
        ex = ExecutiveInteractAction()
        ex.centers = list(center_names if center_names is not None else centers.keys())
        if activation_budget is not None:
            ex.activation_budget = activation_budget
        if denied_text is not None:
            ex.denied_response_text = denied_text
        if enable_transient_ack is not None:
            ex.enable_transient_ack = enable_transient_ack

        async def _lookup(self, name: str):
            return centers.get(name)

        monkeypatch.setattr(ExecutiveInteractAction, "_lookup_center", _lookup)

        if agent is None:
            agent = MagicMock()
            agent.get_access_control_action = AsyncMock(return_value=None)

        async def _get_agent(self):
            return agent

        monkeypatch.setattr(ExecutiveInteractAction, "get_agent", _get_agent)

        action_registry: Dict[str, Any] = dict(centers)

        async def _get_action(self, name):
            return action_registry.get(name)

        ex._test_action_registry = action_registry  # type: ignore[attr-defined]
        monkeypatch.setattr(ExecutiveInteractAction, "get_action", _get_action)

        async def _publish(self, *, visitor, content, **kwargs):
            publish_log.append({"content": content, **kwargs})
            return None

        monkeypatch.setattr(ExecutiveInteractAction, "publish", _publish)

        if router_responses is not None:
            # Drive the REAL _executive_tick via a mocked router model that
            # returns canned JSON (still acquires the per-tick model budget).
            responses = list(router_responses)

            async def _call_router(self, ctx, system_prompt, user_prompt):
                ctx.use_model()
                return responses.pop(0) if responses else None

            monkeypatch.setattr(
                ExecutiveInteractAction, "_call_router_model", _call_router
            )
        else:
            # Scripted executive cognition (bypasses the model entirely).
            script = list(executive_script or [])

            async def _exec_tick(self, ctx):
                if script:
                    return script.pop(0)
                return YIELD()

            monkeypatch.setattr(ExecutiveInteractAction, "_executive_tick", _exec_tick)

        # Inject a fixed capability registry (default: empty).
        from jvagent.action.executive.registry import CapabilityRegistry

        reg = registry if registry is not None else CapabilityRegistry()

        async def _build_registry(self, agent, centers):
            return reg

        monkeypatch.setattr(ExecutiveInteractAction, "_build_registry", _build_registry)

        return ex

    return _factory


@pytest.fixture
def make_visitor():
    def _factory(
        *,
        user_id: str = "user_test",
        channel: str = "default",
        session_id: str = "sess_1",
        utterance: str = "hello",
    ) -> Any:
        interaction = MagicMock()
        interaction.id = "int_1"
        interaction.utterance = utterance
        interaction.response = ""
        interaction.parameters = {}
        interaction.observability_metrics = []
        interaction.record_action_execution = MagicMock()

        conversation = MagicMock()
        conversation.context = {}
        conversation.tasks = []  # real list — TaskStore reads/writes this
        conversation.save = AsyncMock()

        visitor = MagicMock()
        visitor.user_id = user_id
        visitor.channel = channel
        visitor.session_id = session_id
        visitor.interaction = interaction
        visitor.conversation = conversation
        visitor.response_bus = MagicMock()
        visitor.stream = False
        visitor.utterance = utterance
        visitor.prepend = AsyncMock()
        visitor.append = AsyncMock()
        visitor.curate_walk_path = AsyncMock()
        return visitor

    return _factory


@pytest.fixture
def stub_center():
    def _build(
        *,
        name: Optional[str] = None,
        latency_class: str = "quick",
        script: Optional[List[Any]] = None,
        double_model_call: bool = False,
    ) -> StubCenter:
        c = StubCenter()
        if name is not None:
            c.set_name(name)
        c.latency_class = latency_class
        c.set_script(script or [])
        if double_model_call:
            c.set_double_model_call(True)
        return c

    return _build


def make_ac(*, deny_labels: Optional[set] = None, enforcing: bool = True):
    deny = set(deny_labels or [])

    async def _check(*, user_id, action_label, channel):
        return action_label not in deny

    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=enforcing)
    ac.has_action_access = AsyncMock(side_effect=_check)
    return ac


def make_agent_with_ac(ac):
    agent = MagicMock()
    agent.id = "agent_test"
    agent.get_access_control_action = AsyncMock(return_value=ac)
    return agent
