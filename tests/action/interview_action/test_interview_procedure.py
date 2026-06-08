"""Standard interview procedure loading and discovery composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jvagent.core.app_context as app_context
import jvagent.scaffold.skill_resolve as skill_resolve
from jvagent.action.interview_action.core.procedure import (
    compose_interview_skill_body,
    get_standard_interview_procedure,
)
from jvagent.action.orchestrator.skills import discover_skill_docs
from jvagent.scaffold.sop_extend import reset_sop_extend_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_sop_extend_cache()
    yield
    reset_sop_extend_cache()


def test_get_standard_interview_procedure():
    body = get_standard_interview_procedure()
    assert "Standard Interview Procedure" in body
    assert "Answer quality gate" in body
    assert "Intent routing" in body
    assert "Reset tool" in body
    assert "standard ruleset" in body.lower()
    assert "interview__cancel" in body
    assert "substantively answers" in body.lower()
    assert "next_questions" in body
    assert "interview__set_field" in body


def test_compose_interview_skill_body_without_custom():
    standard = get_standard_interview_procedure()
    assert compose_interview_skill_body() == standard
    assert compose_interview_skill_body("   ") == standard


def test_compose_interview_skill_body_with_custom():
    custom = "## Custom instructions\n\nAsk nicely."
    composed = compose_interview_skill_body(custom)
    assert composed.startswith(get_standard_interview_procedure())
    assert composed.endswith(custom)


@pytest.mark.asyncio
async def test_discover_skill_docs_uses_precomposed_body(monkeypatch):
    standard = get_standard_interview_procedure()
    composed = compose_interview_skill_body("## Custom instructions\n\nBe friendly.")

    def _resolve(
        app_root,
        namespace,
        agent_name,
        *,
        include_builtin=True,
        action_refs=None,
    ):
        return {
            "signup_interview": {
                "name": "signup_interview",
                "description": "signup",
                "content": composed,
                "requires_actions": ["InterviewAction"],
                "interview": {"title": "Signup", "questions": [{"name": "user_name"}]},
                "allowed_tools": [
                    "interview__set_field",
                    "interview__get_field",
                    "interview__skip_field",
                    "interview__next_question",
                    "interview__get_status",
                    "interview__review",
                    "interview__complete",
                    "interview__cancel",
                    "interview__reset_interview",
                ],
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
    assert "Intent routing" in docs["signup_interview"].body
    assert "Answer quality gate" in docs["signup_interview"].body
    assert "Cancel vs start over" not in docs["signup_interview"].body
    assert "interview__reset_interview" in docs["signup_interview"].requires_tools
    assert "interview__set_field" in docs["signup_interview"].requires_tools
    assert "## Custom instructions" in docs["signup_interview"].body
    assert "Be friendly." in docs["signup_interview"].body
    assert docs["plain_skill"].body == "Plain SOP only."
