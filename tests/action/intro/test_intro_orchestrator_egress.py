"""IntroInteractAction + orchestrator egress integration.

Pins the first-engagement contract: intro contributes a response parameter on
``visitor.new_user``, and orchestrator egress (via ReplyAction gather/respond)
honors it on the first reply.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.intro.intro_interact_action import IntroInteractAction
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.reply.reply_action import ReplyAction

pytest_plugins = ["tests.action.orchestrator.conftest"]


class _IntroVisitor:
    """Minimal walker stand-in for IntroInteractAction.execute()."""

    def __init__(self, *, new_user: bool, interaction):
        self.new_user = new_user
        self.interaction = interaction
        self.unrecorded = 0

    async def add_parameter(self, parameter):
        self.interaction.parameters.append(
            {**parameter, "executed": False, "action_name": "IntroInteractAction"}
        )

    async def unrecord_action_execution(self):
        self.unrecorded += 1


def _interaction_with_queues():
    interaction = MagicMock()
    interaction.id = "int_intro_1"
    interaction.utterance = "hello"
    interaction.response = ""
    interaction.has_emitted = lambda: bool((interaction.response or "").strip())
    _dirs: list = []
    _params: list = []
    interaction.directives = _dirs
    interaction.parameters = _params

    def _add_directive(content, action_name="ReplyAction"):
        _dirs.append(
            {"action_name": action_name, "content": content, "executed": False}
        )
        return True

    interaction.add_directive = _add_directive
    interaction.get_unexecuted_directives = lambda: [
        d for d in _dirs if not d.get("executed")
    ]
    interaction.get_unexecuted_parameters = lambda: [
        p for p in _params if not p.get("executed")
    ]
    interaction.set_to_executed = MagicMock()
    interaction.utterance = "hello"
    interaction.save = AsyncMock()

    def _set_response(content):
        interaction.response = content
        return True

    interaction.set_response = _set_response
    interaction.mark_emitted = MagicMock()
    return interaction


async def test_intro_adds_parameter_for_new_user_only():
    intro = IntroInteractAction()
    interaction = _interaction_with_queues()

    await intro.execute(_IntroVisitor(new_user=True, interaction=interaction))
    assert len(interaction.parameters) == 1
    assert interaction.parameters[0]["response"] == intro.directive

    interaction.parameters.clear()
    visitor = _IntroVisitor(new_user=False, interaction=interaction)
    await intro.execute(visitor)
    assert interaction.parameters == []
    assert visitor.unrecorded == 1


async def test_orchestrator_egress_compose_with_intro_parameter(monkeypatch):
    """Params-only egress: intro parameter reaches ReplyAction compose."""
    ex = OrchestratorInteractAction()
    reply = ReplyAction()
    interaction = _interaction_with_queues()
    intro = IntroInteractAction()
    await intro.execute(_IntroVisitor(new_user=True, interaction=interaction))

    async def _agent(self):
        return SimpleNamespace(alias="Ada", role="a helpful guide")

    model = MagicMock()
    model.generate = AsyncMock(
        return_value="Hi, I'm Ada — ask me anything about our products."
    )

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_agent", _agent)
    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    monkeypatch.setattr(ReplyAction, "_compose_model_action", _ma)
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=reply)
    )

    visitor = SimpleNamespace(interaction=interaction, channel="web")
    await ex._egress(visitor)

    assert interaction.response == "Hi, I'm Ada — ask me anything about our products."
    sysprompt = model.generate.call_args.kwargs["system"]
    assert intro.directive.split()[0] in sysprompt or "introducing" in sysprompt.lower()


async def test_orchestrator_send_reply_compose_with_intro_and_directive(
    monkeypatch,
):
    """new_user intro param + orchestrator reply directive compose together."""
    intro = IntroInteractAction()
    reply = ReplyAction()
    ex = OrchestratorInteractAction()
    interaction = _interaction_with_queues()
    await intro.execute(_IntroVisitor(new_user=True, interaction=interaction))

    async def _agent(self):
        return SimpleNamespace(alias="Lead Gen Assistant", role="sales helper")

    model = MagicMock()
    model.generate = AsyncMock(
        return_value=("Hi, I'm the Lead Gen Assistant — yes, we offer a free trial.")
    )

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_agent", _agent)
    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    monkeypatch.setattr(ReplyAction, "_compose_model_action", _ma)
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=reply)
    )

    visitor = SimpleNamespace(interaction=interaction, channel="web")
    await ex._send_reply(visitor, "We offer a free trial.")

    assert "free trial" in interaction.response.lower()
    sysprompt = model.generate.call_args.kwargs["system"]
    assert (
        intro.directive.split()[0] in sysprompt or "first message" in sysprompt.lower()
    )
