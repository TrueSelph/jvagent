"""Shared fixtures for Bridge tests.

These fixtures construct a ``BridgeInteractAction`` instance and a fake
``InteractWalker`` without booting an agent graph. The action's ``publish``,
``get_agent``, and ``_lookup_helm`` are monkey-patched so each test exercises
only the step-machine and verb-dispatch logic.

Helm resolution is injected via ``register_helms(bridge, {name: helm})`` —
tests build :class:`StubHelm` instances and hand them in directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.stub_helm import StubHelm


@pytest.fixture
def publish_log() -> List[Dict[str, Any]]:
    """Captured publish() invocations for the test currently running."""
    return []


@pytest.fixture
def make_bridge(monkeypatch, publish_log):
    """Factory that produces a configured ``BridgeInteractAction``.

    Args:
        helms: dict of helm class name → ``BaseHelm`` instance. Used to
            override ``_lookup_helm``.
        helm_names: ordered list of helm names to set on ``bridge.helms``
            (defaults to ``list(helms.keys())``).
        default_helm: optional ``bridge.default_helm`` override.
        agent: optional fake agent returned by ``get_agent``. Defaults to a
            ``MagicMock`` with ``get_access_control_action`` returning ``None``.
        shift_budget: optional override for ``shift_budget_per_turn``.
        first_emit_timeout_ms: optional override for ``first_emit_timeout_ms``.
        denied_text: optional override for ``denied_response_text``.
        safety_text: optional override for ``safety_net_ack_text``.
    """

    def _factory(
        *,
        helms: Optional[Dict[str, BaseHelm]] = None,
        helm_names: Optional[List[str]] = None,
        default_helm: Optional[str] = None,
        agent: Any = None,
        shift_budget: Optional[int] = None,
        first_emit_timeout_ms: Optional[int] = None,
        denied_text: Optional[str] = None,
        safety_text: Optional[str] = None,
    ) -> BridgeInteractAction:
        helms = dict(helms or {})
        bridge = BridgeInteractAction()
        bridge.helms = list(helm_names if helm_names is not None else helms.keys())
        if default_helm is not None:
            bridge.default_helm = default_helm
        if shift_budget is not None:
            bridge.shift_budget_per_turn = shift_budget
        if first_emit_timeout_ms is not None:
            bridge.first_emit_timeout_ms = first_emit_timeout_ms
        if denied_text is not None:
            bridge.denied_response_text = denied_text
        if safety_text is not None:
            bridge.safety_net_ack_text = safety_text

        # Patch helm lookup: returns the test-supplied instance or None.
        async def _lookup(self, name: str):
            return helms.get(name)

        monkeypatch.setattr(BridgeInteractAction, "_lookup_helm", _lookup)

        # Patch get_agent on the instance via class-level patch.
        if agent is None:
            agent = MagicMock()
            agent.get_access_control_action = AsyncMock(return_value=None)

        async def _get_agent(self):
            return agent

        monkeypatch.setattr(BridgeInteractAction, "get_agent", _get_agent)

        # Patch get_action so DELEGATE tests can resolve fake IAs. The
        # registered helms also pass through here in case a future code path
        # calls get_action(helm_name) — keep them addressable.
        action_registry: Dict[str, Any] = dict(helms)

        async def _get_action(self, name):
            return action_registry.get(name)

        # Mutable handle so tests can register fake delegate targets.
        bridge._test_action_registry = action_registry  # type: ignore[attr-defined]
        monkeypatch.setattr(BridgeInteractAction, "get_action", _get_action)

        # Patch publish to record into publish_log instead of touching the bus.
        async def _publish(self, *, visitor, content, **kwargs):
            publish_log.append({"content": content, **kwargs})
            return None

        monkeypatch.setattr(BridgeInteractAction, "publish", _publish)

        return bridge

    return _factory


@pytest.fixture
def make_visitor():
    """Factory for a minimal walker double.

    The walker exposes the attributes Bridge reads — ``user_id``, ``channel``,
    ``response_bus``, ``session_id``, ``interaction``, ``conversation``,
    ``stream`` — and a recorded ``prepend`` ``AsyncMock``. Bridge's state
    plumbing attaches ``_bridge_state`` directly to this object.
    """

    def _factory(
        *,
        user_id: str = "user_test",
        channel: str = "default",
        session_id: str = "sess_1",
    ) -> Any:
        interaction = MagicMock()
        interaction.id = "int_1"
        interaction.utterance = "hello"
        interaction.response = ""
        # Real dict so Bridge's observability persistence
        # (isinstance(params, dict)) writes through. List of dicts
        # would also work; dict is the modern shape.
        interaction.parameters = {}
        interaction.observability_metrics = []

        conversation = MagicMock()

        visitor = MagicMock()
        visitor.user_id = user_id
        visitor.channel = channel
        visitor.session_id = session_id
        visitor.interaction = interaction
        visitor.conversation = conversation
        visitor.response_bus = MagicMock()
        visitor.stream = False
        visitor.utterance = interaction.utterance
        visitor.prepend = AsyncMock()
        visitor.append = AsyncMock()
        return visitor

    return _factory


@pytest.fixture
def stub_helm():
    """Builder for a ``StubHelm`` with a freshly initialised script slot."""

    def _build(
        *,
        name: Optional[str] = None,
        latency_class: str = "quick",
        can_emit_directly: bool = True,
        script: Optional[List[Any]] = None,
    ) -> StubHelm:
        helm = StubHelm()
        if name is not None:
            # Override class-name-based helm_name with a fixed identifier so
            # tests can declare several StubHelm instances and tell them apart.
            object.__setattr__(helm, "_helm_name_override", name)

            def _helm_name(self=helm, _n=name):
                return _n

            # bind per-instance via __dict__ trick (Pydantic-restricted setattr).
            helm.__dict__["helm_name"] = _helm_name
        helm.latency_class = latency_class
        helm.can_emit_directly = can_emit_directly
        helm.set_script(script or [])
        return helm

    return _build


def make_ac(
    *,
    deny_labels: Optional[set] = None,
    enforcing: bool = True,
    default_allow: bool = True,
):
    """Build a fake AccessControlAction for AC tests.

    Mirrors the pattern used in ``tests/action/cockpit/test_access.py``.
    """
    deny = set(deny_labels or [])

    async def _check(*, user_id, action_label, channel):
        if action_label in deny:
            return False
        return default_allow

    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=enforcing)
    ac.has_action_access = AsyncMock(side_effect=_check)
    return ac


def make_agent_with_ac(ac):
    agent = MagicMock()
    agent.id = "agent_test"
    agent.get_access_control_action = AsyncMock(return_value=ac)
    return agent
