"""Complete / set_fields defer when post/complete return status=extraction_pending."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.interview.engine import handle_set_fields
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession, InterviewStatus
from jvagent.action.interview.spec import parse_interview_spec


@pytest.mark.asyncio
async def test_handle_complete_defers_on_extraction_pending(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "custom_tools.py").write_text(
        "async def complete_pending(ctx):\n"
        "    return ctx.tool_response(\n"
        "        ok=True,\n"
        "        status='extraction_pending',\n"
        "        system_message='Still working — wait for callback.',\n"
        "    )\n",
        encoding="utf-8",
    )
    spec = parse_interview_spec(
        {
            "title": "Pending demo",
            "confirm": "auto",
            "handlers": {"complete": "complete_pending"},
            "fields": [
                {
                    "key": "product_urls",
                    "prompt": "Links?",
                    "required": True,
                    "validator": "text",
                }
            ],
        },
        source_dir=str(tmp_path),
        default_name="pending_demo",
    )
    action = InterviewAction()
    action._registry._specs["pending_demo"] = spec
    session = InterviewSession(interview_type="pending_demo")
    session.status = InterviewStatus.REVIEW
    session.set_value("product_urls", "https://www.amazon.com/dp/X")
    session.context["url_queue"] = ["https://www.shein.com/x"]

    conv = SimpleNamespace(context={"interview": {"keep": True}}, save=AsyncMock())
    visitor = SimpleNamespace(conversation=conv, utterance="ok")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    action._clear_interview_session = AsyncMock()

    with patch(
        "jvagent.action.interview.engine.tasks.close_task", new=AsyncMock()
    ) as close_mock:
        result = json.loads(await action._handle_complete(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "extraction_pending"
    assert "Interview completed" not in str(result.get("response_directive") or "")
    assert "wait for callback" in str(result.get("system_message") or "").lower()
    close_mock.assert_not_awaited()
    action._clear_interview_session.assert_not_awaited()
    assert session.context.get("url_queue") == ["https://www.shein.com/x"]


@pytest.mark.asyncio
async def test_set_fields_skips_review_chain_on_extraction_pending(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "custom_tools.py").write_text(
        "async def process_urls(ctx):\n"
        "    ctx.say('Still checking your first link — I will update you shortly.')\n"
        "    return ctx.tool_response(\n"
        "        ok=True,\n"
        "        status='extraction_pending',\n"
        "        system_message='Async pending — do not complete.',\n"
        "    )\n",
        encoding="utf-8",
    )
    spec = parse_interview_spec(
        {
            "title": "Quote demo",
            "confirm": "auto",
            "fields": [
                {
                    "key": "product_urls",
                    "prompt": "Links?",
                    "required": True,
                    "validator": "text",
                    "post_processor": "process_urls",
                }
            ],
        },
        source_dir=str(tmp_path),
        default_name="quote_demo",
    )
    action = InterviewAction()
    action._registry._specs["quote_demo"] = spec
    session = InterviewSession(interview_type="quote_demo")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(conversation=SimpleNamespace(context={}))

    raw = await handle_set_fields(
        action,
        fields={"product_urls": "https://www.amazon.com/dp/X"},
        visitor=visitor,
    )
    result = json.loads(raw)

    assert result["ok"] is True
    assert result["status"] == "extraction_pending"
    assert result.get("next_tool") not in (
        "interview__review",
        "interview__complete",
    )
    directive = str(result.get("response_directive") or "").lower()
    assert "interview__review" not in directive
    assert "interview__complete" not in directive
    assert "still checking" in directive
    assert "do not complete" in str(result.get("system_message") or "").lower()
