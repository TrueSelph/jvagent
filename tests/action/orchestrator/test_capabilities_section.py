"""Capabilities are self-declared by actions via the base ``Action.get_capabilities``
and aggregated by the orchestrator (across enabled actions) with the skill
descriptions to build the "WHAT YOU CAN DO" digest — complete regardless of lean
tool surfacing, so the model never under-claims ("I can't sign you up…").
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.base import Action
from jvagent.action.orchestrator.prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    render_capabilities_section,
)

pytestmark = pytest.mark.asyncio


def test_base_action_advertises_nothing_by_default():
    assert Action().get_capabilities() == []


def test_render_formats_dedupes_and_caps():
    out = render_capabilities_section(
        [
            "Sign users up for training",
            "Sign users up for training",  # dup
            "First line.\nSecond line dropped.",
            "z" * 200,
            "",
            None,
        ]
    )
    assert "- Sign users up for training" in out
    assert out.count("Sign users up for training") == 1  # de-duped
    assert "Second line" not in out  # first line only
    assert "z" * 200 not in out  # length-capped
    # empties skipped, never blank (would break the prompt slot)
    assert out.strip()


def test_render_empty_has_safe_fallback():
    assert render_capabilities_section([]).strip()


def test_digest_slots_into_system_prompt():
    cap = render_capabilities_section(["Sign users up for training"])
    prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(
        identity_section="You are X. ",
        tools_section="(tools)",
        skills_section="(skills)",
        capabilities_section=cap,
        parameters_section="(rules)",
        loop_protocol_extra="",
    )
    assert "Sign users up for training" in prompt
    assert "WHAT YOU CAN DO" in prompt


def test_interview_action_advertises_itself():
    from jvagent.action.interview.interview_action import (
        InterviewAction,
    )

    a = InterviewAction()
    a.description = "Training signup interview"
    assert a.get_capabilities() == ["Training signup interview"]


async def test_agent_collect_capabilities_aggregates_and_dedupes(monkeypatch):
    """The Agent is the single aggregation point — it flattens each enabled
    action's get_capabilities() into one de-duplicated, ordered list."""
    from jvagent.core.agent import Agent

    class _Cap(Action):
        def get_capabilities(self):
            return ["Sign users up for training"]

    class _Dup(Action):
        def get_capabilities(self):
            return ["Sign users up for training"]  # duplicate — collapsed

    class _Plumbing(Action):
        pass  # default [] — advertises nothing

    async def _actions(self, enabled_only=False):
        return [_Cap(), _Dup(), _Plumbing()]

    monkeypatch.setattr(Agent, "get_actions", _actions)

    agent = Agent(namespace="x", name="y", alias="A", description="d")
    assert await agent.collect_capabilities() == ["Sign users up for training"]


async def test_orchestrator_merges_agent_caps_with_skills(monkeypatch):
    """The orchestrator delegates action aggregation to the agent and only
    appends skill descriptions on top."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    ex = OrchestratorInteractAction()

    class _Agent:
        async def collect_capabilities(self):
            return ["Sign users up for training"]

    monkeypatch.setattr(
        OrchestratorInteractAction, "_safe_agent", AsyncMock(return_value=_Agent())
    )

    skills = [SimpleNamespace(name="research", description="Research a topic")]
    caps = await ex._collect_capabilities(skills)

    assert caps == ["Sign users up for training", "Research a topic"]
