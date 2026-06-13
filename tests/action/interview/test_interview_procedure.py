"""Standard interview procedure loading and discovery composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jvagent.core.app_context as app_context
import jvagent.scaffold.skill_resolve as skill_resolve
from jvagent.action.interview.procedure import (
    compose_interview_skill_body,
    get_standard_interview_procedure,
)
from jvagent.action.orchestrator.skills import discover_skill_docs
from jvagent.scaffold.sop_extend import reset_sop_extend_cache
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_sop_extend_cache()
    yield
    reset_sop_extend_cache()


def test_get_standard_interview_procedure():
    body = get_standard_interview_procedure()
    assert "Standard Interview Procedure" in body
    assert "Intent → tool" in body
    assert "Correct / update" in body
    assert "interview__cancel" in body
    assert "interview__set_fields" in body
    assert "interview__reset" in body


def test_standard_procedure_includes_session_gate():
    body = get_standard_interview_procedure()
    assert "Session gate" in body
    assert "use_skill" in body
    assert "No session → no field prompts in `reply`" in body
    assert "Activation:" in body
    assert "field_awareness" not in body
    assert "quoted `field_key` only" not in body
    assert "all extracted keys" in body
    assert "Do not invent or alias keys" in body
    assert "available_times` not `availability" in body
    assert "Unknown key recovery" in body
    assert "activation returns `awaiting_fields` only" not in body


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
                "interview": {
                    "title": "Signup",
                    "fields": [{"key": "user_name", "prompt": "Name?"}],
                },
                "allowed_tools": [
                    "interview__set_fields",
                    "interview__get_status",
                    "interview__skip_field",
                    "interview__next_field",
                    "interview__get_status",
                    "interview__review",
                    "interview__complete",
                    "interview__cancel",
                    "interview__reset",
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
    assert "Intent → tool" in docs["signup_interview"].body
    assert "Branching" in docs["signup_interview"].body
    assert "interview__reset" in docs["signup_interview"].requires_tools
    assert "interview__set_fields" in docs["signup_interview"].requires_tools
    assert "## Custom instructions" in docs["signup_interview"].body
    assert "Be friendly." in docs["signup_interview"].body
    assert docs["plain_skill"].body == "Plain SOP only."


def test_signup_skill_custom_instructions_model_owned_flow():
    """Orchestrator signup_interview SOP matches stripped harness (no prep steering)."""
    skill_md = SIGNUP_INTERVIEW_SKILL_DIR / "SKILL.md"
    custom_body = skill_md.read_text(encoding="utf-8").split("---", 2)[-1].strip()
    composed = compose_interview_skill_body(custom_body)

    assert "interview__set_fields" in composed
    assert "interview__reset" in composed
    assert "Correct / update" in composed
    assert "interview__message_evaluation" not in composed
    assert "Chaining" in composed
    assert "Session gate" in composed
    assert "Branching" in composed
    assert "Anti-drip rule" in composed
    assert "available_times" in composed
    assert "jvagent training" in composed.lower()


def test_base_sop_references_field_reference():
    from jvagent.action.interview.procedure import get_standard_interview_procedure

    body = get_standard_interview_procedure()
    assert "field_reference" in body
    # Compound-extraction rule lives here (sole home after orchestrator removal).
    assert "Submit one initial" in body
    assert "every extracted key/value" in body
