"""The interview owns its divergence handling: on an off-topic turn (Intent.NONE)
QuestionNode prepends the interview's rendered ``active_task_description`` to the
question directive, so the reply follows the interview's policy (default: answer
naturally; override: answer briefly then re-ask) before re-asking the pending
field. A valid answer (SUBMISSION) must NOT get the guidance (no deflection).
Centralized in the interview so every host gets it with no host-side wiring.
"""

from unittest.mock import patch

import pytest

from jvagent.action.interview.core.foundation.enums import Intent
from jvagent.action.interview.core.graph.question_node import QuestionNode

pytestmark = pytest.mark.asyncio

GUIDANCE = "STAY ON SCRIPT: answer the aside in one line, then re-ask."


class _Session:
    def get_answered_questions(self):
        return set()


class _Action:
    question_directive = "Ask: {question}{context_section}{instructions}"
    required_field_decline = ""

    def render_active_task_guidance(self):
        return GUIDANCE


class _Walker:
    def __init__(self, intent):
        self.interview_session = _Session()
        self.interview_action = _Action()
        self.current_intent = intent


async def _no_context(self, *a, **k):
    return {}


def _node():
    n = QuestionNode()
    n.state = {
        "name": "user_email",
        "question": "What is your email?",
        "required": True,
        "constraints": {"description": "email", "instructions": ""},
    }
    return n


async def _render(intent):
    with patch.object(QuestionNode, "get_context_data", _no_context):
        return await _node().execute(_Walker(intent))


async def test_off_topic_prepends_active_task_guidance():
    d = await _render(Intent.NONE)
    assert d and d.startswith(GUIDANCE)
    assert "What is your email?" in d  # still re-asks the pending field


async def test_valid_answer_uses_plain_directive():
    d = await _render(Intent.SUBMISSION)
    assert d and d.startswith("Ask:")
    assert GUIDANCE not in d


async def test_no_intent_uses_plain_directive():
    d = await _render(None)
    assert d and GUIDANCE not in d


async def test_render_active_task_guidance_fills_placeholders():
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

    a = InterviewInteractAction()
    a.active_task_description = (
        "Mid {action_title} ({action_description}); answer briefly then re-ask."
    )
    a.description = "Training signup interview"
    object.__setattr__(a, "metadata", {"title": "SignupInterviewInteractAction"})
    out = a.render_active_task_guidance()
    assert "Mid Signup" in out
    assert "Training signup interview" in out
    assert "{action_title}" not in out
