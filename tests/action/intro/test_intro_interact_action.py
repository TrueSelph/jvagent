"""Tests for IntroInteractAction.

The intro contributes its first-message self-introduction as a response-shaping
*parameter* (HOW), not a *directive* (WHAT), so ReplyAction weaves the greeting
into the same reply as the substantive answer instead of emitting a separate
mandated section. These tests pin that contract with a lightweight fake visitor.
"""

import pytest

from jvagent.action.intro.intro_interact_action import IntroInteractAction


class _FakeVisitor:
    def __init__(self, *, new_user=True, interaction=object()):
        self.new_user = new_user
        self.interaction = interaction
        self.parameters = []
        self.directives = []
        self.unrecorded = 0

    async def add_parameter(self, parameter):
        self.parameters.append(parameter)

    async def add_directive(self, directive):
        self.directives.append(directive)

    async def unrecord_action_execution(self):
        self.unrecorded += 1


@pytest.mark.asyncio
async def test_new_user_adds_parameter_not_directive():
    action = IntroInteractAction()
    visitor = _FakeVisitor(new_user=True)

    await action.execute(visitor)

    assert visitor.directives == []  # no standalone directive
    assert len(visitor.parameters) == 1
    param = visitor.parameters[0]
    assert param["response"] == action.directive
    # Unconditional (interaction-scoped): the action already gates on new_user.
    assert "condition" not in param or not param["condition"]


@pytest.mark.asyncio
async def test_returning_user_contributes_nothing():
    action = IntroInteractAction()
    visitor = _FakeVisitor(new_user=False)

    await action.execute(visitor)

    assert visitor.parameters == []
    assert visitor.directives == []
    assert visitor.unrecorded == 1


@pytest.mark.asyncio
async def test_no_interaction_skips():
    action = IntroInteractAction()
    visitor = _FakeVisitor(new_user=True, interaction=None)

    await action.execute(visitor)

    assert visitor.parameters == []
    assert visitor.unrecorded == 1


@pytest.mark.asyncio
async def test_empty_directive_skips():
    action = IntroInteractAction()
    action.directive = ""
    visitor = _FakeVisitor(new_user=True)

    await action.execute(visitor)

    assert visitor.parameters == []
    assert visitor.unrecorded == 1


def test_healthcheck_requires_directive():
    action = IntroInteractAction()
    assert action.directive  # default is set

    action.directive = ""
    # healthcheck is async
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(action.healthcheck())
    assert isinstance(result, dict) and result.get("status") is False
