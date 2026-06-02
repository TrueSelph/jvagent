"""The default question directive must NOT echo the field ``description`` into the
question (the "What's your full name? (The user's full name)" leak). The clean
core default drops the inline ``({description})``; QuestionNode folds the field
description into a non-echoed Note instead, so nothing is lost. A template that
DOES reference {description} still echoes it (back-compat for explicit overrides).
"""

from unittest.mock import patch

import pytest

from jvagent.action.interview.core.foundation.enums import Intent
from jvagent.action.interview.core.foundation.prompts import QUESTION_DIRECTIVE
from jvagent.action.interview.core.graph.question_node import QuestionNode

pytestmark = pytest.mark.asyncio


class _Session:
    def get_answered_questions(self):
        return set()


class _Action:
    question_directive = QUESTION_DIRECTIVE  # the clean core default
    required_field_decline = ""

    def render_active_task_guidance(self):
        return ""


class _Walker:
    def __init__(self):
        self.interview_session = _Session()
        self.interview_action = _Action()
        self.current_intent = Intent.SUBMISSION


async def _no_context(self, *a, **k):
    return {}


def _node():
    n = QuestionNode()
    n.state = {
        "name": "user_full_name",
        "question": "What's your full name?",
        "required": True,
        "constraints": {"description": "The user's full name", "instructions": ""},
    }
    return n


def test_default_question_directive_has_no_inline_description():
    assert "({description})" not in QUESTION_DIRECTIVE
    assert "{description}" not in QUESTION_DIRECTIVE


async def test_default_directive_does_not_echo_description():
    with patch.object(QuestionNode, "get_context_data", _no_context):
        d = await _node().execute(_Walker())
    assert "What's your full name?" in d
    # the description is NOT echoed inline into the question (no leak) ...
    assert "(The user's full name)" not in d
    # ... but it is preserved as non-echoed guidance for the model.
    assert "This field captures: The user's full name." in d


async def test_template_that_uses_description_still_echoes_it():
    """An explicit override that keeps {description} still gets it (back-compat),
    and the folded Note is NOT added (no duplication)."""
    action = _Action()
    action.question_directive = "Ask: {question} ({description}){instructions}"
    walker = _Walker()
    walker.interview_action = action
    with patch.object(QuestionNode, "get_context_data", _no_context):
        d = await _node().execute(walker)
    assert "(The user's full name)" in d
    assert "This field captures:" not in d
