"""Tests for jvvoice HTTP bridge."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent_bridge import (
    extract_interact_response_text,
    interact,
    jvagent_base_url,
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


class TestExtractInteractResponseText:
    def test_top_level_response(self):
        body = {
            "success": True,
            "user_id": "u1",
            "session_id": "s1",
            "response": "Hello from orchestrator",
        }
        assert extract_interact_response_text(body) == "Hello from orchestrator"

    def test_data_wrapped_response(self):
        body = {
            "success": True,
            "data": {
                "response": "Wrapped reply",
                "user_id": "u1",
            },
        }
        assert extract_interact_response_text(body) == "Wrapped reply"

    def test_interaction_fallback(self):
        body = {
            "success": True,
            "interaction": {"response": "From interaction object"},
        }
        assert extract_interact_response_text(body) == "From interaction object"


class TestJvagentBaseUrl:
    def test_uses_per_call_override(self):
        assert (
            jvagent_base_url("https://tenant.example.com/")
            == "https://tenant.example.com"
        )

    def test_missing_override_raises(self, monkeypatch):
        # No env fallback: a missing per-call URL is a hard error.
        monkeypatch.setenv("JVAGENT_BASE_URL", "https://default.example.com")
        with pytest.raises(ValueError):
            jvagent_base_url()


@pytest.mark.asyncio
async def test_interact_uses_per_call_base_url():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "response": "Hello from tenant",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "jvagent_bridge.httpx.AsyncClient",
        return_value=mock_client,
    ):
        text = await interact(
            agent_id="n.Agent.test",
            utterance="Hi",
            user_id="16315553601",
            session_id="whatsapp-call:16315553601",
            jvagent_base_url_override="https://tenant-a.example.com",
        )
    assert text == "Hello from tenant"
    posted_url = mock_client.post.await_args.args[0]
    assert posted_url == "https://tenant-a.example.com/api/agents/n.Agent.test/interact"


@pytest.mark.asyncio
async def test_interact_parses_top_level_response():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "user_id": "u1",
        "session_id": "s1",
        "response": "Hello from orchestrator",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "jvagent_bridge.httpx.AsyncClient",
        return_value=mock_client,
    ):
        text = await interact(
            agent_id="n.Agent.test",
            utterance="Hi",
            user_id="16315553601",
            session_id="whatsapp-call:16315553601",
            jvagent_base_url_override="https://tenant-a.example.com",
        )
    assert text == "Hello from orchestrator"


@pytest.mark.asyncio
async def test_interact_missing_base_url_raises():
    with pytest.raises(ValueError):
        await interact(
            agent_id="n.Agent.test",
            utterance="Hi",
            user_id="16315553601",
            session_id="whatsapp-call:16315553601",
        )
