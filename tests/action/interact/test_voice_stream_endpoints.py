"""Tests for the real-time STT WebSocket (``/agents/{id}/voice/stt/stream``).

Covers the session-token gate (query param), the audio→transcript bridge, and
that :func:`register_voice_ws_routes` adds a route that survives app rebuilds
(it wraps the factory, not the built app).
"""

import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import jvagent.action.interact.voice_stream_endpoints as vs


class _FakeSTT:
    """Stand-in STT provider with a streaming method that echoes canned events."""

    async def stream_transcribe(self, audio_iter, on_event, *, language=None):
        # Drain the audio (mirrors the real provider consuming the queue).
        async for _chunk in audio_iter:
            pass
        await on_event({"type": "interim", "transcript": "hello"})
        await on_event({"type": "final", "transcript": "hello world"})


def _patch_valid_session(monkeypatch, stt=_FakeSTT()):
    monkeypatch.setattr(
        vs,
        "verify_session_token",
        lambda tok, expected_agent_id: ({"session_id": "s1"}, None),
    )
    monkeypatch.setattr(vs, "_load_conversation", _async_return(object()))
    monkeypatch.setattr(vs, "claims_match_conversation", lambda claims, conv: None)
    monkeypatch.setattr(vs, "resolve_agent_action", _async_return(stt))
    # get_cached_agent is imported lazily inside the handler.
    import jvagent.core.cache as cache

    monkeypatch.setattr(cache, "get_cached_agent", _async_return(object()))


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def _app() -> FastAPI:
    app = FastAPI()
    app.add_api_websocket_route(
        "/api/agents/{agent_id}/voice/stt/stream", vs.stt_stream_handler
    )
    return app


def test_missing_token_is_rejected(monkeypatch):
    # An absent token must be rejected before the socket is accepted.
    monkeypatch.setattr(
        vs, "verify_session_token", lambda tok, expected_agent_id: (None, "missing")
    )
    client = TestClient(_app())
    # A pre-accept close surfaces to the TestClient as WebSocketDisconnect.
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/agents/n.Agent.1/voice/stt/stream"),
    ):
        pass


def test_stream_bridges_audio_to_transcripts(monkeypatch):
    _patch_valid_session(monkeypatch)
    client = TestClient(_app())
    with client.websocket_connect(
        "/api/agents/n.Agent.1/voice/stt/stream?token=good"
    ) as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x00\x01\x02")
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "interim", "transcript": "hello"}
        assert ws.receive_json() == {"type": "final", "transcript": "hello world"}


def test_provider_without_streaming_reports_unavailable(monkeypatch):
    class _NoStream:
        pass

    _patch_valid_session(monkeypatch, stt=_NoStream())
    client = TestClient(_app())
    with client.websocket_connect(
        "/api/agents/n.Agent.1/voice/stt/stream?token=good"
    ) as ws:
        assert ws.receive_json() == {
            "type": "error",
            "message": "stt_streaming_unavailable",
        }


def test_register_voice_ws_routes_adds_route_to_every_built_app():
    """The route must ride the factory so it survives rebuilds."""

    class _FakeServer:
        def _create_app_instance(self):
            return FastAPI()

    server = _FakeServer()
    vs.register_voice_ws_routes(server)

    def _has_ws_route(app):
        return any(
            getattr(r, "path", None) == "/api/agents/{agent_id}/voice/stt/stream"
            for r in app.router.routes
        )

    # Two independent builds (initial + a simulated rebuild) both carry the route.
    assert _has_ws_route(server._create_app_instance())
    assert _has_ws_route(server._create_app_instance())
