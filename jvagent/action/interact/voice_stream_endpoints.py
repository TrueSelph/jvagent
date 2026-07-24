"""Real-time speech-to-text WebSocket for the embeddable messenger.

``WS /agents/{agent_id}/voice/stt/stream`` streams mic audio from the browser to
the agent's STT provider and streams interim + final transcripts back, so the
composer fills in live as the user speaks.

Why a hand-registered route instead of ``@endpoint``: jvspatial's ``@endpoint``
decorator is HTTP-only, and the framework rebuilds its FastAPI app from the HTTP
endpoint registry on dynamic changes — a route added to a built app would be
dropped on the next rebuild. :func:`register_voice_ws_routes` therefore wraps the
server's app factory so the WS route is present on *every* app instance the
factory produces (initial build and every rebuild). Wired in from
``jvagent.cli.server_config`` right after the ``Server`` is constructed.

Auth mirrors the POST voice/upload endpoints (always require a valid Mode B
session capability token), but browsers cannot set custom headers on a WebSocket
handshake, so the token rides as the ``token`` query param and is verified with
:func:`verify_session_token` directly. A token in a query string is only
acceptable over ``wss://`` in production.

Client protocol (see jvmessenger ``voiceStreamClient.ts``):
  client → server : binary frames = raw webm/opus chunks; a text frame
                    ``{"type":"stop"}`` signals end-of-audio.
  server → client : text JSON — ``{"type":"ready"}`` / ``{"type":"interim",…}``
                    / ``{"type":"final",…}`` / ``{"type":"utterance_end"}`` /
                    ``{"type":"error","message":…}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, AsyncIterator

from fastapi import WebSocket, WebSocketDisconnect

from jvagent.action.interact.public_gate import (
    _load_conversation,
    resolve_agent_action,
)
from jvagent.action.interact.rate_limiter import extract_client_ip, get_rate_limiter
from jvagent.action.interact.session_token import (
    claims_match_conversation,
    verify_session_token,
)

logger = logging.getLogger(__name__)

# WebSocket close codes (application range 4000-4999).
_WS_UNAUTHORIZED = 4401
_WS_NOT_FOUND = 4404
_WS_RATE_LIMITED = 4429


async def stt_stream_handler(websocket: WebSocket, agent_id: str) -> None:
    """Bridge a browser mic WebSocket to the agent's live STT provider."""
    # 1) Rate limit (WS is not covered by the HTTP rate-limit middleware).
    rate_limiter = get_rate_limiter()
    client_ip = extract_client_ip(websocket) or "unknown"
    if not await rate_limiter.check_rate_limit(client_ip, agent_id):
        await websocket.close(code=_WS_RATE_LIMITED)
        return
    await rate_limiter.record_request(client_ip, agent_id)

    # 2) Session-token gate (query param — WS handshakes carry no custom headers).
    token = (websocket.query_params.get("token") or "").strip()
    claims, err = verify_session_token(token, expected_agent_id=agent_id)
    if err or claims is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    if not agent:
        await websocket.close(code=_WS_NOT_FOUND)
        return

    # 3) Token must still bind to its web-owned conversation.
    conv = await _load_conversation(agent, str(claims.get("session_id") or ""))
    if claims_match_conversation(claims, conv):
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    stt = await resolve_agent_action(agent, "BaseSTTAction")
    if stt is None or not hasattr(stt, "stream_transcribe"):
        # Accept then report so the client can fall back to batch STT.
        await websocket.accept()
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "message": "stt_streaming_unavailable"}
            )
        await websocket.close()
        return

    await websocket.accept()
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "ready"})

    # Bridge: ws_reader pushes inbound audio onto a queue; stream_transcribe pulls
    # from it and pushes transcripts back out via on_event.
    queue: asyncio.Queue = asyncio.Queue()

    async def audio_iter() -> AsyncIterator[bytes]:
        while True:
            item = await queue.get()
            if item is None:  # sentinel = end of audio
                return
            yield item

    async def on_event(event: dict) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_json(event)

    async def ws_reader() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await queue.put(data)
                    continue
                text = message.get("text")
                if text is not None:
                    try:
                        control = json.loads(text)
                    except (ValueError, TypeError):
                        control = {}
                    if control.get("type") == "stop":
                        break
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("stt stream reader error: %s", exc)
        finally:
            await queue.put(None)

    reader_task = asyncio.create_task(ws_reader())
    try:
        await stt.stream_transcribe(audio_iter(), on_event)
    except Exception as exc:  # provider error must not leak a 500 on the socket
        logger.warning("stt stream transcribe error: %s", exc)
        await on_event({"type": "error", "message": "stt_stream_failed"})
    finally:
        reader_task.cancel()
        with contextlib.suppress(Exception):
            await reader_task
        with contextlib.suppress(Exception):
            await websocket.close()


def register_voice_ws_routes(server: Any) -> None:
    """Add the STT streaming WS route to every app the server's factory builds.

    Wraps ``server._create_app_instance`` (the single construction path used for
    the initial app and every dynamic rebuild) so the route persists — a route
    added to an already-built app would be lost on the next rebuild. Instance-
    level (not a class monkeypatch), mirroring how jvagent decorates the server
    before ``.run()``.
    """
    from jvspatial.api.constants import APIRoutes

    prefix = str(APIRoutes.PREFIX).rstrip("/")
    path = f"{prefix}/agents/{{agent_id}}/voice/stt/stream"
    original = server._create_app_instance

    def _patched_create_app_instance() -> Any:
        app = original()
        app.add_api_websocket_route(path, stt_stream_handler)
        return app

    server._create_app_instance = _patched_create_app_instance
    logger.debug("Registered STT streaming WebSocket route at %s", path)
