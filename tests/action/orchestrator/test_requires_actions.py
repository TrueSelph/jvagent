"""``requires-actions`` enforcement (hard gate): a JV skill whose declared
Action types don't all resolve (enabled) on the agent — and, when an inline
PEP 508-style version constraint is given, whose resolved Action version doesn't
satisfy it — is hidden from the whole surface (never listed, found, activated,
or always-active-pinned)."""

from __future__ import annotations

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
    _parse_action_requirement,
    _version_satisfies,
)
from jvagent.action.orchestrator.skills import SkillDoc


class _FakeAction:
    def __init__(self, version: str = ""):
        self._version = version

    async def get_version(self) -> str:
        return self._version


def _doc(name, requires_actions=()):
    return SkillDoc(
        name=name,
        description="",
        body="",
        requires_actions=tuple(requires_actions),
    )


def _patch_resolver(monkeypatch, present):
    """Resolve only the named types. ``present`` is a set (version-less) or a
    dict mapping type name -> reported version string."""

    async def _fake(self, name):
        if isinstance(present, dict):
            return _FakeAction(present[name]) if name in present else None
        return _FakeAction() if name in present else None

    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_action", _fake)


# -- spec parsing ----------------------------------------------------------


def test_parse_bare_name():
    assert _parse_action_requirement("CodeExecutionAction") == (
        "CodeExecutionAction",
        "",
    )


def test_parse_operator_delimited():
    assert _parse_action_requirement("PageIndexAction>=2.0") == (
        "PageIndexAction",
        ">=2.0",
    )
    assert _parse_action_requirement("WebFetchAction==1.4.0") == (
        "WebFetchAction",
        "==1.4.0",
    )
    assert _parse_action_requirement("X>=1.0,<2.0") == ("X", ">=1.0,<2.0")


def test_version_satisfies():
    assert _version_satisfies("2.1", ">=2.0") is True
    assert _version_satisfies("1.5", ">=2.0") is False
    assert _version_satisfies("1.4.0", "==1.4.0") is True
    assert _version_satisfies("1.4.1", "==1.4.0") is False
    assert _version_satisfies("1.5", "") is True  # no constraint -> presence only
    assert _version_satisfies("", ">=2.0") is False  # constrained but no version
    # Unparseable constraint degrades to presence-only (don't nuke on a typo).
    assert _version_satisfies("1.0", "not-a-constraint") is True


# -- enforcement -----------------------------------------------------------


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


async def test_version_constraint_satisfied_keeps_skill(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present={"PageIndexAction": "2.1"})
    docs = [_doc("research", ["PageIndexAction>=2.0"])]
    out = await ex._enforce_required_actions(docs)
    assert [d.name for d in out] == ["research"]


async def test_version_constraint_unsatisfied_hides_skill(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present={"PageIndexAction": "1.5"})
    docs = [_doc("research", ["PageIndexAction>=2.0"])]
    out = await ex._enforce_required_actions(docs)
    assert out == []


async def test_present_but_unversioned_action_fails_a_constraint(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(monkeypatch, present={"PageIndexAction": ""})  # no version
    docs = [_doc("needs_ver", ["PageIndexAction>=2.0"])]
    out = await ex._enforce_required_actions(docs)
    assert out == []


async def test_mixed_specs(monkeypatch):
    ex = OrchestratorInteractAction()
    _patch_resolver(
        monkeypatch,
        present={"WebFetchAction": "1.4.0", "SerperWebSearchAction": "0.9"},
    )
    docs = [
        # WebFetch satisfies ==1.4.0; Serper bare name is presence-only.
        _doc("ok", ["WebFetchAction==1.4.0", "SerperWebSearchAction"]),
        # Serper 0.9 fails >=1.0 -> hidden.
        _doc("blocked", ["SerperWebSearchAction>=1.0"]),
    ]
    out = await ex._enforce_required_actions(docs)
    assert [d.name for d in out] == ["ok"]
