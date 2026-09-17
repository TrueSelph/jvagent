"""Ready-notify answers from PageIndex document content, not keyword search."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.artifact_handler_interact_action import endpoints as ep

_CALL_MODEL_MOD = importlib.import_module("jvagent.action.utils.call_model")


def _agent(page_index=None, agent_id="agent-1"):
    return SimpleNamespace(
        id=agent_id,
        get_action_by_type=AsyncMock(return_value=page_index),
    )


def _page_index(*, search_results=None, collection="agent-1"):
    search = AsyncMock(
        return_value=search_results if search_results is not None else []
    )
    return SimpleNamespace(
        resolve_collection=lambda: collection,
        search=search,
    )


@pytest.mark.asyncio
async def test_ready_message_uses_chunks_when_search_empty(monkeypatch):
    captured = {}

    async def fake_chunks(doc_name, collection, **_kwargs):
        assert doc_name == "user_photo.jpg"
        assert collection == "agent-1"
        return {
            "chunks": [
                {
                    "title": "Photo",
                    "text": "A water bottle, car keys, and a brown wallet on a table.",
                }
            ],
            "total": 1,
        }

    async def fake_call_model(_action, user_prompt, system_prompt):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return (
            "Your image is ready. You asked what the items in the pic are. "
            "A water bottle, car keys, and a brown wallet."
        )

    page_index = _page_index(search_results=[])
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.list_document_chunks",
        fake_chunks,
    )
    monkeypatch.setattr(_CALL_MODEL_MOD, "call_model", fake_call_model)

    text = await ep._generate_ready_message(
        agent=_agent(page_index),
        vault_action=SimpleNamespace(),
        internal_doc_name="user_photo.jpg",
        display_doc="photo.jpg",
        utterance="what are the items in the pic",
    )

    assert "water bottle" in text
    assert "excerpts" not in text.lower()
    assert "excerpts do not contain" not in captured["system"].lower()
    assert "Search excerpts" not in captured["user"]
    assert "Document content" in captured["user"]
    assert "water bottle" in captured["user"]
    page_index.search.assert_not_called()


@pytest.mark.asyncio
async def test_ready_message_pdf_uses_chunks_when_query_would_not_match(monkeypatch):
    captured = {}

    async def fake_chunks(_doc_name, _collection, **_kwargs):
        return {
            "chunks": [
                {
                    "title": "Lab policy",
                    "text": "Safety goggles are required in all wet labs.",
                }
            ],
            "total": 1,
        }

    async def fake_call_model(_action, user_prompt, system_prompt):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return (
            "Your PDF is ready. You asked what the policy says. "
            "Safety goggles are required in all wet labs."
        )

    page_index = _page_index(search_results=[])
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.list_document_chunks",
        fake_chunks,
    )
    monkeypatch.setattr(_CALL_MODEL_MOD, "call_model", fake_call_model)

    text = await ep._generate_ready_message(
        agent=_agent(page_index),
        vault_action=SimpleNamespace(),
        internal_doc_name="lab_policy.pdf",
        display_doc="policy.pdf",
        utterance="what are the items in the pic",
    )

    assert "goggles" in text
    assert "excerpts do not contain" not in captured["system"].lower()
    assert "Safety goggles" in captured["user"]
    page_index.search.assert_not_called()


@pytest.mark.asyncio
async def test_ready_message_empty_chunks_no_excerpts_wording(monkeypatch):
    call_model = AsyncMock(return_value="should not run")

    async def fake_chunks(_doc_name, _collection, **_kwargs):
        return {"chunks": [], "total": 0}

    page_index = _page_index(search_results=[])
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.list_document_chunks",
        fake_chunks,
    )
    monkeypatch.setattr(_CALL_MODEL_MOD, "call_model", call_model)

    text = await ep._generate_ready_message(
        agent=_agent(page_index),
        vault_action=SimpleNamespace(),
        internal_doc_name="user_photo.jpg",
        display_doc="photo.jpg",
        utterance="what are the items in the pic",
    )

    assert text is not None
    assert "excerpts" not in text.lower()
    assert "couldn't read any content" in text.lower()
    assert "You asked: what are the items in the pic" in text
    call_model.assert_not_called()
    page_index.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_message_search_supplement_when_chunks_empty(monkeypatch):
    captured = {}

    async def fake_chunks(_doc_name, _collection, **_kwargs):
        return {"chunks": [], "total": 0}

    async def fake_call_model(_action, user_prompt, system_prompt):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return (
            "Your image is ready. You asked about the items. "
            "The photo shows a calculator and a ruler."
        )

    page_index = _page_index(
        search_results=[
            {"title": "Scan", "content": "A calculator and a ruler."},
        ]
    )
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.list_document_chunks",
        fake_chunks,
    )
    monkeypatch.setattr(_CALL_MODEL_MOD, "call_model", fake_call_model)

    text = await ep._generate_ready_message(
        agent=_agent(page_index),
        vault_action=SimpleNamespace(),
        internal_doc_name="scan.png",
        display_doc="scan.png",
        utterance="what are the items in the pic",
    )

    assert "calculator" in text
    assert "excerpts do not contain" not in captured["system"].lower()
    assert "Search excerpts" not in captured["user"]
    assert "A calculator and a ruler." in captured["user"]
    page_index.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_message_multi_loads_each_doc_chunks(monkeypatch):
    captured = {}

    async def fake_chunks(doc_name, _collection, **_kwargs):
        bodies = {
            "a.jpg": "Red apple and a banana.",
            "b.pdf": "Office hours are 9 to 5.",
        }
        return {
            "chunks": [{"title": doc_name, "text": bodies[doc_name]}],
            "total": 1,
        }

    async def fake_call_model(_action, user_prompt, system_prompt):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return (
            "Your image and your PDF are ready. You asked about the fruit "
            "and the hours. Apple and banana; 9 to 5."
        )

    page_index = _page_index(search_results=[])
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.list_document_chunks",
        fake_chunks,
    )
    monkeypatch.setattr(_CALL_MODEL_MOD, "call_model", fake_call_model)

    text = await ep._generate_ready_message_multi(
        agent=_agent(page_index),
        vault_action=SimpleNamespace(),
        ready_entries=[
            {
                "internal_doc_name": "a.jpg",
                "display_doc": "fruit.jpg",
                "pending_question": "what fruit is that",
            },
            {
                "internal_doc_name": "b.pdf",
                "display_doc": "hours.pdf",
                "pending_question": "when are office hours",
            },
        ],
    )

    assert "Apple" in text or "apple" in text.lower()
    assert "excerpts do not contain" not in captured["system"].lower()
    assert "Search excerpts" not in captured["user"]
    assert "Document content:" in captured["user"]
    assert "Red apple and a banana." in captured["user"]
    assert "Office hours are 9 to 5." in captured["user"]
    page_index.search.assert_not_called()


def test_truncate_ready_text_caps_large_docs():
    body = "\n".join(f"line {i} content" for i in range(2000))
    out = ep._truncate_ready_text(body, max_chars=200)
    assert len(out) <= 210
    assert out.endswith("…")
