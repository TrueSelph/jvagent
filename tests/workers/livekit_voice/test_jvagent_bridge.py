"""Tests for jvagent voice worker HTTP bridge."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.livekit_voice.jvagent_bridge import (
    interact,
    parse_dispatch_metadata,
    session_id_for_caller,
)


class TestSessionIdForCaller:
    def test_stable_session(self):
        assert session_id_for_caller("16315553601") == "whatsapp-call:16315553601"


class TestParseDispatchMetadata:
    def test_parses_json(self):
        meta = parse_dispatch_metadata(
            '{"jvagent_agent_id": "n.Agent.x", "caller_phone": "1"}'
        )
        assert meta["jvagent_agent_id"] == "n.Agent.x"
        assert meta["caller_phone"] == "1"

    def test_invalid_returns_empty(self):
        assert parse_dispatch_metadata("not-json") == {}


@pytest.mark.asyncio
async def test_interact_parses_wrapped_response():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "success": True,
            "data": {
                "response": "Hello from orchestrator",
                "user_id": "u1",
                "session_id": "s1",
            },
        },
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "workers.livekit_voice.jvagent_bridge.httpx.AsyncClient",
        return_value=mock_client,
    ):
        text = await interact(
            agent_id="n.Agent.test",
            utterance="Hi",
            user_id="16315553601",
            session_id="whatsapp-call:16315553601",
        )
    assert text == "Hello from orchestrator"
