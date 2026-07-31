"""A blocked always-active skill must stay out of in-force and out of the prompt.

`_enforce_required_actions` hides a skill whose ``requires-actions`` don't
resolve, and `_prepare_turn` computes the always-active in-force set FROM the
filtered ``skill_docs`` — so a library skill shipped with ``always-active: true``
(artifact_handler is the first) does not force heavy gear or absorb parameters
on agents that can't run it.

That wiring is load-bearing and was, until now, untested: the channel-gating
tests mock ``_enforce_required_actions`` away, and this repo's recurring failure
shape is precisely "the filter is correct but a consumer reads the unfiltered
list". A false alarm during the post-merge review (a probe that called
``discover_skill_docs`` raw and concluded gearing was broken fleet-wide) showed
how expensive the ambiguity is — this pins the truth.
"""

from typing import Any, Dict, List, Set
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc

pytestmark = pytest.mark.asyncio


def _wire_minimal_surface(monkeypatch: pytest.MonkeyPatch, orch, docs) -> None:
    """The same minimal harness the channel-gating tests use — but with the
    REAL ``_enforce_required_actions`` left in place; that filter is the thing
    under test."""
    monkeypatch.setattr(orch, "_discover_skills", lambda _agent: list(docs))
    # No action of any type resolves → every requires-actions spec is unmet.
    monkeypatch.setattr(
        OrchestratorInteractAction, "_resolve_action", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_enabled_actions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_safe_agent", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_select_code_execution_action",
        staticmethod(lambda _actions: None),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )


async def _run_assemble(orch) -> tuple:
    visible: Set[str] = set()
    skill_docs: List[Any] = []
    surface_meta: Dict[str, Any] = {}
    visitor = MagicMock()
    visitor.channel = "web"
    tools = await orch._assemble_tools(
        visitor, [], visible, None, "hello there", skill_docs, surface_meta
    )
    return tools, skill_docs, surface_meta


async def test_blocked_always_active_skill_never_reaches_skill_docs(monkeypatch):
    """`skill_docs` is what _prepare_turn derives in-force (and therefore gear
    and parameter absorption) from. A hidden skill must not be in it."""
    orch = OrchestratorInteractAction()
    blocked = SkillDoc(
        name="vault_like",
        description="library skill needing actions this agent lacks",
        body="SOP",
        always_active=True,
        requires_actions=("NoSuchAction", "AlsoMissingAction"),
    )
    plain = SkillDoc(name="faq", description="FAQs", body="SOP")
    _wire_minimal_surface(monkeypatch, orch, [blocked, plain])

    _tools, skill_docs, _meta = await _run_assemble(orch)

    names = {getattr(d, "name", "") for d in skill_docs}
    assert "faq" in names
    assert "vault_like" not in names

    # The exact expression _prepare_turn uses (loop.py) — must come up empty.
    in_force = [d for d in skill_docs if getattr(d, "always_active", False)]
    assert in_force == []


async def test_blocked_skill_without_deny_directive_costs_no_prompt_note(monkeypatch):
    """Requires-blocked skills are dropped silently; only channel-blocked skills
    WITH a deny-access-directive rent prompt space. A library skill installed
    fleet-wide must not tax unrelated agents' prompts."""
    orch = OrchestratorInteractAction()
    blocked = SkillDoc(
        name="vault_like",
        description="d",
        body="SOP",
        always_active=True,
        requires_actions=("NoSuchAction",),
    )
    _wire_minimal_surface(monkeypatch, orch, [blocked])

    _tools, skill_docs, meta = await _run_assemble(orch)

    assert skill_docs == []
    assert meta.get("blocked_skill_notes", []) == []


async def test_met_requirements_keep_the_always_active_skill(monkeypatch):
    """The other direction: on an agent that HAS the required actions, the
    always-active skill stays — hiding it everywhere would break the feature."""
    orch = OrchestratorInteractAction()
    doc = SkillDoc(
        name="vault_like",
        description="d",
        body="SOP",
        always_active=True,
        requires_actions=("PresentAction",),
    )
    _wire_minimal_surface(monkeypatch, orch, [doc])

    present = MagicMock()
    present.get_version = AsyncMock(return_value="1.0.0")
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_resolve_action",
        AsyncMock(return_value=present),
    )

    _tools, skill_docs, _meta = await _run_assemble(orch)

    in_force = [d for d in skill_docs if getattr(d, "always_active", False)]
    assert [d.name for d in in_force] == ["vault_like"]
