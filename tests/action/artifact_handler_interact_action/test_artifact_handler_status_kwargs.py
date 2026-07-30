"""artifact_handler status tools tolerate model-invented kwargs."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jvagent.action.artifact_handler_interact_action import (
    ArtifactHandlerInteractAction,
)


@pytest.mark.asyncio
async def test_check_ingest_status_ignores_url_and_doc_name():
    """Models often pass url/doc_name; status is pending-job based and must not TypeError."""
    action = ArtifactHandlerInteractAction()
    action._dispatch_tool = AsyncMock(return_value='{"ok": true, "status": "queued"}')

    tools = {t.name: t for t in await action.get_tools()}
    status_tool = tools["artifact_handler__check_ingest_status"]

    out = await status_tool.execute(
        url="https://docs.google.com/document/d/abc/edit",
        doc_name="Some Doc",
    )
    assert "queued" in out
    action._dispatch_tool.assert_awaited_once()
    kwargs = action._dispatch_tool.await_args.kwargs
    assert kwargs.get("visitor") is None or "visitor" in kwargs
    assert "url" not in kwargs
    assert "doc_name" not in kwargs
    assert action._dispatch_tool.await_args.args[0] == "check_ingest_status"


@pytest.mark.asyncio
async def test_check_pending_attachments_ignores_extra_kwargs():
    action = ArtifactHandlerInteractAction()
    action._dispatch_tool = AsyncMock(return_value='{"ok": true, "pending": false}')

    tools = {t.name: t for t in await action.get_tools()}
    pending_tool = tools["artifact_handler__check_pending_attachments"]

    out = await pending_tool.execute(
        url="https://example.com/x.pdf",
        doc_name="x.pdf",
        whatever="extra",
    )
    assert "pending" in out
    action._dispatch_tool.assert_awaited_once()
    kwargs = action._dispatch_tool.await_args.kwargs
    assert "url" not in kwargs
    assert "doc_name" not in kwargs
    assert action._dispatch_tool.await_args.args[0] == "check_pending_attachments"
