"""jvforge assimilate: retry transient httpx transport errors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.action.pageindex.jvforge_assimilate import (
    _jvforge_form_data,
    _post_jvforge_with_retries,
    assimilate_via_jvforge_async,
)


def test_jvforge_form_data_notification_passthrough() -> None:
    """notification_url / notification_secret are included in the multipart form."""
    data = _jvforge_form_data(
        agent_id="agent-1",
        doc_name="doc.docx",
        model=None,
        if_add_node_summary="no",
        collection_name="agent-1",
        metadata=None,
        doc_description=None,
        doc_url=None,
        convert_to_markdown=True,
        ocr=False,
        normalize_bold_headings=False,
        llm_webhook_url="http://localhost/webhook",
        notification_url="https://hooks.example/notify",
        notification_secret="s3cret",
    )
    assert data["notification_url"] == "https://hooks.example/notify"
    assert data["notification_secret"] == "s3cret"


def test_jvforge_form_data_omits_notification_when_unset() -> None:
    data = _jvforge_form_data(
        agent_id="agent-1",
        doc_name="doc.docx",
        model=None,
        if_add_node_summary="no",
        collection_name="agent-1",
        metadata=None,
        doc_description=None,
        doc_url=None,
        convert_to_markdown=True,
        ocr=False,
        normalize_bold_headings=False,
        llm_webhook_url="http://localhost/webhook",
    )
    assert "notification_url" not in data
    assert "notification_secret" not in data


def _ok_queued_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {
        "job_id": "job-1",
        "status": "queued",
        "doc_name": "doc.docx",
        "queue_position": {"overall": 1, "per_agent": 1},
    }
    resp.text = ""
    return resp


@pytest.mark.asyncio
async def test_post_jvforge_retries_read_error_then_succeeds() -> None:
    client = AsyncMock()
    client.post = AsyncMock(
        side_effect=[
            httpx.ReadError("connection reset"),
            httpx.ReadError("connection reset"),
            _ok_queued_response(),
        ]
    )
    with patch(
        "jvagent.action.pageindex.jvforge_assimilate.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        resp = await _post_jvforge_with_retries(
            client,
            "http://jvforge.test/v1/jobs",
            headers={},
            data={"agent_id": "a"},
            files={"file": ("doc.docx", b"bytes", "application/octet-stream")},
        )
    assert resp.status_code == 202
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_post_jvforge_raises_validation_after_retries_exhausted() -> None:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ReadError("still down"))
    with patch(
        "jvagent.action.pageindex.jvforge_assimilate.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        with pytest.raises(ValidationError) as ei:
            await _post_jvforge_with_retries(
                client,
                "http://jvforge.test/v1/jobs",
                headers={},
                data={"agent_id": "a"},
                files={"file": ("doc.docx", b"bytes", "application/octet-stream")},
            )
    assert "connection error" in str(ei.value.message)
    assert "JVAGENT_JVFORGE_BASE_URL" in str(ei.value.message)
    assert client.post.await_count == 5


@pytest.mark.asyncio
async def test_assimilate_via_jvforge_async_retries_read_error() -> None:
    ok = _ok_queued_response()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=[httpx.ReadError("boom"), httpx.ReadError("boom"), ok]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "jvagent.action.pageindex.jvforge_assimilate.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch(
            "jvagent.action.pageindex.jvforge_assimilate.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        result = await assimilate_via_jvforge_async(
            base_url="http://jvforge.test",
            agent_id="agent-1",
            doc_name="doc.docx",
            model=None,
            if_add_node_summary="no",
            collection_name="agent-1",
            metadata=None,
            doc_description=None,
            doc_url=None,
            convert_to_markdown=True,
            ocr=True,
            llm_webhook_url="http://localhost/webhook",
            filename="doc.docx",
            content=b"%docx",
        )

    assert result["status"] == "queued"
    assert result["job_id"] == "job-1"
    assert mock_client.post.await_count == 3
