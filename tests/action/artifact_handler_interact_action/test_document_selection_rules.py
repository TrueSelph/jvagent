"""OPERATING RULES force faq + scoped search on document follow-ups."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (
    DOCUMENT_CONTENT_CONDITION,
    DOCUMENT_SELECTION_RULES,
    ArtifactHandlerInteractAction,
)


def test_selection_rules_require_faq_then_scoped_search():
    text = DOCUMENT_SELECTION_RULES
    assert "use_skill" in text
    assert "faq" in text
    assert "pageindex__search" in text
    assert "Active document" in text
    assert "doc_name" in text


def test_selection_rules_forbid_answering_from_descriptions_or_history():
    text = DOCUMENT_SELECTION_RULES.lower()
    assert "do not answer from prior replies" in text
    assert "[event]" in text
    assert "doc_description" in text
    assert "not facts to quote" in text


def test_content_condition_includes_continuations():
    cond = DOCUMENT_CONTENT_CONDITION
    assert "continues a prior document question" in cond
    assert "the last 2" in cond
    assert "and/also" in cond


@pytest.mark.asyncio
async def test_inject_parameter_uses_selection_rules():
    captured = {}

    async def add_parameter(param):
        captured.update(param)

    visitor = SimpleNamespace(
        interaction=object(),
        conversation=SimpleNamespace(
            context={"artifact_handler": {"active_doc_name": "user_notes.jpg"}}
        ),
        add_parameter=add_parameter,
    )
    page_index = SimpleNamespace(
        list_documents=AsyncMock(
            return_value=[
                {
                    "doc_name": "user_notes.jpg",
                    "doc_description": "work notes including tour comparison",
                }
            ]
        )
    )
    action = ArtifactHandlerInteractAction()
    await action._inject_accessible_documents_parameter(
        visitor, page_index, "user-1", "sess-1"
    )

    assert captured["scope"] == "orchestration"
    assert captured["condition"] == DOCUMENT_CONTENT_CONDITION
    response = captured["response"]
    assert "Active document: user_notes.jpg" in response
    assert DOCUMENT_SELECTION_RULES in response
    assert "work notes including tour comparison" in response
    assert "use_skill" in response
    assert "pageindex__search" in response
