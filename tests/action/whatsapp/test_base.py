"""Tests for WhatsApp base module - ConnectionPoolManager and event loop handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.whatsapp.modules.base import ConnectionPoolManager
from jvagent.action.whatsapp.modules.wwebjs_api import WWebJSAPI


class TestConnectionPoolEventLoop:
    """Tests for connection pool event-loop validation and invalidation."""

    @pytest.mark.asyncio
    async def test_get_session_returns_fresh_when_stored_loop_invalid(self):
        """When stored session's event loop is closed/different, get_session returns fresh."""
        from urllib.parse import urlparse

        pool = ConnectionPoolManager()
        parsed = urlparse("https://api.example.com")
        pool_key = (parsed.netloc, 30)

        # Get initial session (valid)
        session1 = await pool.get_session("https://api.example.com", 30.0)
        session1_id = id(session1)

        # Inject a session with invalid loop (simulates Lambda/uvicorn reload)
        mock_session = MagicMock()
        mock_session.closed = False
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        mock_session._loop = mock_loop

        async with pool._session_lock:
            pool._sessions[pool_key] = mock_session

        # get_session should detect invalid loop and return fresh session
        session2 = await pool.get_session("https://api.example.com", 30.0)
        session2_id = id(session2)

        assert (
            session1_id != session2_id
        ), "Should return fresh session when loop invalid"
        assert session2 is not mock_session, "Should not return stale mock session"

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_make_request_invalidates_pool_on_event_loop_closed_and_retries(self):
        """When request fails with 'Event loop is closed', pool is invalidated and retry succeeds."""
        # Mock pool that returns a session raising on first use
        first_call = {"value": True}

        class SuccessCtx:
            """Async context manager returning success response."""

            async def __aenter__(self):
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.content_length = 0
                mock_resp.read = AsyncMock(return_value=b"")
                mock_resp.text = AsyncMock(return_value="")
                mock_resp.json = AsyncMock(return_value={"success": True})
                return mock_resp

            async def __aexit__(self, *a):
                pass

        def mock_request(*args, **kwargs):
            if first_call["value"]:
                first_call["value"] = False
                raise RuntimeError("Event loop is closed")
            return SuccessCtx()

        mock_session = MagicMock()
        mock_session.request = mock_request
        mock_session.closed = False

        mock_pool = MagicMock()
        mock_pool.get_session = AsyncMock(return_value=mock_session)
        mock_pool.close_session = AsyncMock()

        with (
            patch(
                "jvagent.action.whatsapp.modules.base.get_connection_pool",
                return_value=mock_pool,
            ),
            patch(
                "jvagent.action.whatsapp.modules.base.aiohttp.ClientSession"
            ) as mock_client_cls,
        ):
            # Fresh session for retry
            fresh_mock = MagicMock()
            fresh_mock.request = mock_request
            fresh_mock.closed = False
            mock_client_cls.return_value = fresh_mock

            api = WWebJSAPI(
                api_url="http://localhost:9999",
                session="test",
                token="x",
                secret_key="sk",
            )
            result = await api._make_request(
                "http://localhost:9999/ping",
                "GET",
                {},
                json_body=False,
            )

        assert result.get("ok") or result.get(
            "success"
        ), f"Expected success, got {result}"
        mock_pool.close_session.assert_called_once_with("http://localhost:9999", 10.0)
