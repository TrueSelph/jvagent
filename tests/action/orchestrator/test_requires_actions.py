"""``requires-actions`` enforcement (hard gate): a JV skill whose declared
Action types don't all resolve (enabled) on the agent is hidden from the whole
surface — never listed, found, activated, or always-active-pinned."""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc

pytestmark = pytest.mark.asyncio


def _doc(name, requires_actions=()):
    return SkillDoc(
        name=name,
        description="",
        body="",
        requires_actions=tuple(requires_actions),
    )


def _patch_resolver(monkeypatch, present):
    """Make ``_resolve_action`` resolve only the named types in ``present``."""

    async def _fake(self, name):
        return object() if name in present else None

    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_action", _fake)


async def test_skilldoc_carries_requires_actions():
    d = _doc("x", ["CodeExecutionAction"])
    assert d.requires_actions == ("CodeExecutionAction",)


async def test_no_requirements_pass_through():
    ex = OrchestratorInteractAction()
    docs = [_doc("a"), _doc("b")]
    out = await ex._enforce_required_actions(docs)
    assert [d.name for d in out] == ["a", "b"]


async def test_skill_hidden_when_a_required_action_is_missing(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present={"CodeExecutionAction"})
    docs = [
        _doc("ok", ["CodeExecutionAction"]),
        _doc("blocked", ["CodeExecutionAction", "PageIndexAction"]),
        _doc("plain"),  # no requirements -> always kept
    ]
    out = await ex._enforce_required_actions(docs)
    assert [d.name for d in out] == ["ok", "plain"]


async def test_all_present_keeps_skill(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present={"SerperWebSearchAction", "WebFetchAction"})
    docs = [_doc("research", ["SerperWebSearchAction", "WebFetchAction"])]
    out = await ex._enforce_required_actions(docs)
    assert [d.name for d in out] == ["research"]


async def test_all_required_missing_hides_skill(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present=set())
    docs = [_doc("research", ["SerperWebSearchAction"])]
    out = await ex._enforce_required_actions(docs)
    assert out == []
