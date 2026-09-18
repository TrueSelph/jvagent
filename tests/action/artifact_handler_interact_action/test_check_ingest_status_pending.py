"""Other-channel check_ingest_status answers pending questions in-process."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.artifact_handler_interact_action.vault_tool_context import (
    VaultToolContext,
)

_CUSTOM_TOOLS_PATH = (
    Path(__file__).resolve().parents[3]
    / "jvagent"
    / "skills"
    / "artifact_handler"
    / "scripts"
    / "custom_tools.py"
)

_DOC_NAME = (
    "o.User.5f0e3d12a28547ec9e240f1f_WhatsApp_Image_2026-09-04_at_4.17.25_PM.jpeg"
)
_PENDING_Q = "what is the first item in the photo"
_ANSWER = (
    "Your image is ready. You asked about the first item in the photo. "
    "It is a water bottle."
)


def _load_custom_tools():
    spec = importlib.util.spec_from_file_location(
        "artifact_handler_custom_tools_pending_test", _CUSTOM_TOOLS_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ct = _load_custom_tools()


class FakeConversation:
    def __init__(self, pending):
        self.context = {
            "artifact_handler": {"pending_ingest_jobs": pending},
        }

    async def update_context(self, data):
        for key, value in data.items():
            if key == "artifact_handler" and isinstance(value, dict):
                vault = dict(self.context.get("artifact_handler") or {})
                vault.update(value)
                self.context["artifact_handler"] = vault
            else:
                self.context[key] = value


def _pending_job(*, status="ready"):
    return {
        "job-1": {
            "doc_name": _DOC_NAME,
            "status": status,
            "submitted_at": 1789667107,
            "pending_question": _PENDING_Q,
        }
    }


def _ctx(conversation, action=None):
    interaction = SimpleNamespace(events=[])

    def _add_event(event, action_name):
        interaction.events.append({"action_name": action_name, "content": event})
        return True

    interaction.add_event = _add_event
    interaction.save = AsyncMock()
    visitor = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        conversation=conversation,
        interaction=interaction,
    )
    return VaultToolContext(
        visitor=visitor, args={}, action=action or SimpleNamespace()
    )


async def _no_refresh(*_args, **_kwargs):
    return {"refreshed": 0, "became_ready": [], "still_queued": [], "failed": []}


async def _empty_desc(*_args, **_kwargs):
    return {}


@pytest.mark.asyncio
async def test_ready_pending_question_emits_generated_answer(monkeypatch):
    conv = FakeConversation(_pending_job())
    ctx = _ctx(conv)

    async def fake_answer(_ctx, ready_questions, _desc_lookup):
        assert ready_questions[0]["question"] == _PENDING_Q
        assert ready_questions[0]["doc_name"] == _DOC_NAME
        return _ANSWER

    monkeypatch.setattr(ct, "_maybe_refresh_pending_jobs", _no_refresh)
    monkeypatch.setattr(ct, "_doc_description_lookup", _empty_desc)
    monkeypatch.setattr(ct, "_generate_poll_ready_answer", fake_answer)

    raw = await ct.check_ingest_status(ctx)
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["became_ready"] == []
    directive = payload["response_directive"]
    assert directive.startswith("Tell the user:")
    assert "water bottle" in directive
    assert "pageindex__search" not in directive
    assert "Call pageindex__search" not in payload.get("system_message", "")
    assert payload["jobs"][0]["pending_question"] is None
    stored = conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]
    assert "pending_question" not in stored
    assert ctx._directives == [f"Tell the user: {_ANSWER}"]
    assert ctx.visitor.interaction.events
    assert _DOC_NAME in ctx.visitor.interaction.events[0]["content"]


@pytest.mark.asyncio
async def test_already_ready_empty_became_ready_still_answers(monkeypatch):
    conv = FakeConversation(_pending_job(status="ready"))
    ctx = _ctx(conv)

    monkeypatch.setattr(ct, "_maybe_refresh_pending_jobs", _no_refresh)
    monkeypatch.setattr(ct, "_doc_description_lookup", _empty_desc)
    monkeypatch.setattr(
        ct, "_generate_poll_ready_answer", AsyncMock(return_value=_ANSWER)
    )

    payload = json.loads(await ct.check_ingest_status(ctx))
    assert payload["became_ready"] == []
    assert payload["still_queued"] == []
    assert "water bottle" in payload["response_directive"]
    assert "pageindex__search" not in payload["response_directive"]


@pytest.mark.asyncio
async def test_generation_failure_keeps_pending_question_and_search(monkeypatch):
    conv = FakeConversation(_pending_job())
    ctx = _ctx(conv)

    monkeypatch.setattr(ct, "_maybe_refresh_pending_jobs", _no_refresh)
    monkeypatch.setattr(ct, "_doc_description_lookup", _empty_desc)
    monkeypatch.setattr(ct, "_generate_poll_ready_answer", AsyncMock(return_value=None))

    payload = json.loads(await ct.check_ingest_status(ctx))
    assert payload["status"] == "ready"
    assert payload["jobs"][0]["pending_question"] == _PENDING_Q
    assert "pageindex__search" in payload["response_directive"]
    assert "pending_question" in payload.get("system_message", "")
    stored = conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]
    assert stored["pending_question"] == _PENDING_Q
    assert ctx.visitor.interaction.events == []


@pytest.mark.asyncio
async def test_poll_ready_answer_calls_shared_helper(monkeypatch):
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return _ANSWER

    monkeypatch.setattr(
        "jvagent.action.artifact_handler_interact_action.ready_message._generate_ready_message",
        fake_generate,
    )

    action = SimpleNamespace(get_agent=AsyncMock(return_value=SimpleNamespace(id="a1")))
    ctx = _ctx(FakeConversation(_pending_job()), action=action)
    text = await ct._generate_poll_ready_answer(
        ctx,
        [{"job_id": "job-1", "doc_name": _DOC_NAME, "question": _PENDING_Q}],
        {},
    )
    assert text == _ANSWER
    assert captured["internal_doc_name"] == _DOC_NAME
    assert captured["utterance"] == _PENDING_Q


def test_artifact_handler_tools_are_trusted_directive_source():
    from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (  # noqa: F401
        _register_orchestrator_vocabulary,
    )
    from jvagent.action.orchestrator.constants import is_untrusted_directive_source

    _register_orchestrator_vocabulary()
    assert (
        is_untrusted_directive_source("artifact_handler__check_ingest_status") is False
    )
