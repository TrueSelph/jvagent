"""Tests for jvvoice HTTP delegation client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from jvagent.action.whatsapp_voice.jvvoice_client import (
    JvvoiceClient,
    JvvoiceClientError,
)


@pytest.mark.asyncio
async def test_accept_call_posts_bearer_token():
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "connected", "room_name": "room-1"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "jvagent.action.whatsapp_voice.jvvoice_client.httpx.AsyncClient",
        return_value=mock_client,
    ):
        client = JvvoiceClient(
            base_url="https://jvvoice.example.com",
            api_key="secret",
        )
        result = await client.accept_call({"jvagent_agent_id": "n.Agent.x"})

    assert result["status"] == "connected"
    call_kwargs = mock_client.post.await_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer secret"
    assert (
        mock_client.post.await_args.args[0]
        == "https://jvvoice.example.com/api/calls/accept"
    )


@pytest.mark.asyncio
async def test_accept_call_raises_on_http_error():
    request = httpx.Request("POST", "https://jvvoice.example.com/api/calls/accept")
    response = httpx.Response(500, request=request, text="boom")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("err", request=request, response=response)
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "jvagent.action.whatsapp_voice.jvvoice_client.httpx.AsyncClient",
        return_value=mock_client,
    ):
        client = JvvoiceClient(base_url="https://jvvoice.example.com", api_key="secret")
        with pytest.raises(JvvoiceClientError):
            await client.accept_call({})
