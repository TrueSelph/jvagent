"""Tests for IntroInteractAction.

The intro contributes its first-message self-introduction as orchestration-
and response-scoped *parameters* (HOW), not a *directive* (WHAT), so the
executive sees the custom intro when authoring the reply and ReplyAction can
still weave the greeting into the same reply as the substantive answer.
"""

import pytest

from jvagent.action.intro.intro_interact_action import IntroInteractAction
from jvagent.action.parameters import SCOPE_ORCHESTRATION, SCOPE_RESPONSE


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
async def test_new_user_adds_dual_scope_parameters_not_directive():
    action = IntroInteractAction()
    visitor = _FakeVisitor(new_user=True)

    await action.execute(visitor)

    assert visitor.directives == []  # no standalone directive
    assert len(visitor.parameters) == 2
    scopes = {p["scope"] for p in visitor.parameters}
    assert scopes == {SCOPE_ORCHESTRATION, SCOPE_RESPONSE}
    for param in visitor.parameters:
        assert param["key"] == "intro.first_message"
        assert param["response"] == action.directive
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


@pytest.mark.asyncio
async def test_healthcheck_requires_directive():
    action = IntroInteractAction()
    assert action.directive  # default is set

    action.directive = ""
    result = await action.healthcheck()
    assert isinstance(result, dict) and result.get("status") is False


# --- greet exactly once -----------------------------------------------------


def test_intro_directive_still_asks_for_the_introduction():
    """Two rounds of tightening this wording to stop the double greeting made the
    model drop the introduction instead (3 of 4 live runs). The wording stays as
    the version that reliably introduces; the duplicate greeting is settled
    deterministically in vet_egress instead."""
    from jvagent.action.intro.intro_interact_action import IntroInteractAction

    directive = IntroInteractAction().directive.lower()
    assert "introducing yourself by name" in directive
    assert "knowledge cutoff" in directive
