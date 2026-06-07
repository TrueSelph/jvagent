"""Standard interview procedure loading and discovery composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jvagent.core.app_context as app_context
import jvagent.scaffold.skill_resolve as skill_resolve
from jvagent.action.interview_action.procedure import (
    compose_interview_skill_body,
    get_standard_interview_procedure,
    is_interview_skill_bundle,
)
from jvagent.action.orchestrator.skills import discover_skill_docs


def test_get_standard_interview_procedure_cached():
    first = get_standard_interview_procedure()
    second = get_standard_interview_procedure()
    assert first == second
    assert "Standard Interview Procedure" in first
    assert "next_questions" in first
    assert "interview__set_field" in first


def test_compose_interview_skill_body_without_custom():
    standard = get_standard_interview_procedure()
    assert compose_interview_skill_body() == standard
    assert compose_interview_skill_body("   ") == standard


def test_compose_interview_skill_body_with_custom():
    custom = "## Custom instructions\n\nAsk nicely."
    composed = compose_interview_skill_body(custom)
    assert composed.startswith(get_standard_interview_procedure())
    assert composed.endswith(custom)


def test_is_interview_skill_bundle():
    assert is_interview_skill_bundle(
        {
            "requires_actions": ["InterviewAction"],
            "interview": {"questions": []},
        }
    )
    assert not is_interview_skill_bundle(
        {
            "requires_actions": ["InterviewAction"],
            "interview": None,
        }
    )
    assert not is_interview_skill_bundle(
        {
            "requires_actions": ["OtherAction"],
            "interview": {"questions": []},
        }
    )


@pytest.mark.asyncio
async def test_discover_skill_docs_composes_interview_body(monkeypatch):
    def _resolve(app_root, namespace, agent_name, *, include_builtin=True):
        return {
            "signup_interview": {
                "name": "signup_interview",
                "description": "signup",
                "content": "## Custom instructions\n\nBe friendly.",
                "requires_actions": ["InterviewAction"],
                "interview": {"title": "Signup", "questions": [{"name": "user_name"}]},
                "allowed_tools": [],
                "source": "app",
                "metadata": {},
            },
            "plain_skill": {
                "name": "plain_skill",
                "description": "plain",
                "content": "Plain SOP only.",
                "requires_actions": [],
                "allowed_tools": [],
                "source": "app",
                "metadata": {},
            },
        }

    monkeypatch.setattr(app_context, "get_app_root", lambda: "/fake/app")
    monkeypatch.setattr(skill_resolve, "resolve_merged_skill_bundles", _resolve)

    agent = SimpleNamespace(namespace="jvagent", name="orchestrator_agent")
    docs = {
        d.name: d
        for d in discover_skill_docs(agent, skills_source="app", selector="-all")
    }

    assert "Standard Interview Procedure" in docs["signup_interview"].body
    assert "## Custom instructions" in docs["signup_interview"].body
    assert "Be friendly." in docs["signup_interview"].body
    assert docs["plain_skill"].body == "Plain SOP only."
