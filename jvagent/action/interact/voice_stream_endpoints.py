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

Auth: browsers cannot set custom headers on a WebSocket handshake, so the client
first ``POST``s ``/voice/stt/stream/ticket`` with ``X-Session-Token`` (header-
authed) to mint a short-lived ticket, then opens the socket with ``?ticket=``.
Legacy ``?token=`` (full session capability token) is still accepted for older
clients, but the ticket path keeps long-lived secrets out of access logs /
Referer chains.

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
from typing import Any, AsyncIterator, Optional, Tuple

from fastapi import Request, WebSocket, WebSocketDisconnect
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ValidationError

from jvagent.action.interact.public_gate import (
    _load_conversation,
    require_messenger_session,
    resolve_agent_action,
)
from jvagent.action.interact.rate_limiter import extract_client_ip, get_rate_limiter
from jvagent.action.interact.session_token import (
    claims_match_conversation,
    mint_stt_stream_ticket,
    stt_stream_ticket_ttl_seconds,
    verify_session_token,
    verify_stt_stream_ticket,
)

logger = logging.getLogger(__name__)

# WebSocket close codes (application range 4000-4999).
_WS_UNAUTHORIZED = 4401
_WS_NOT_FOUND = 4404
_WS_RATE_LIMITED = 4429
_WS_OVERLOAD = 4429  # reuse rate-limit code for queue overflow / budget

# Bound inbound audio so a valid session cannot OOM the worker.
# 250 ms MediaRecorder slices → ~16 s of backlog at maxsize=64.
_MAX_AUDIO_QUEUE = 64
_MAX_AUDIO_BYTES = 8 * 1024 * 1024  # 8 MiB per stream session


@endpoint(
    "/agents/{agent_id}/voice/stt/stream/ticket",
    methods=["POST"],
    auth=False,
    tags=["Agent"],
)
async def stt_stream_ticket_endpoint(request: Request, agent_id: str) -> Any:
    """Mint a short-lived WS ticket from a header-authed session token.

    The long-lived Mode B token stays in ``X-Session-Token`` (never logged as a
    query param); the ticket is what rides on the WebSocket URL.
    """
    _agent, claims = await require_messenger_session(request, agent_id)
    ticket = mint_stt_stream_ticket(
        agent_id=agent_id,
        session_id=str(claims.get("session_id") or ""),
        user_id=str(claims.get("user_id") or ""),
        token_secret=str(claims.get("cs") or ""),
    )
    if not ticket:
        raise ValidationError(
            message="Session tickets are unavailable (signing secret not configured).",
            details={"reason": "no_secret_configured"},
        )
    ttl = stt_stream_ticket_ttl_seconds()
    return {"ticket": ticket, "expires_in": ttl}


async def _claims_from_ws_query(
    websocket: WebSocket, agent_id: str
) -> Tuple[Optional[dict], Optional[str]]:
    """Resolve WS auth from ``?ticket=`` (preferred) or legacy ``?token=``."""
    ticket = (websocket.query_params.get("ticket") or "").strip()
    if ticket:
        return verify_stt_stream_ticket(ticket, expected_agent_id=agent_id)
    token = (websocket.query_params.get("token") or "").strip()
    if token:
        return verify_session_token(token, expected_agent_id=agent_id)
    return None, "missing_token"


async def stt_stream_handler(websocket: WebSocket, agent_id: str) -> None:
    """Bridge a browser mic WebSocket to the agent's live STT provider."""
    # 1) Rate limit (WS is not covered by the HTTP rate-limit middleware).
    rate_limiter = get_rate_limiter()
    client_ip = extract_client_ip(websocket) or "unknown"
    if not await rate_limiter.check_rate_limit(client_ip, agent_id):
        await websocket.close(code=_WS_RATE_LIMITED)
        return
    await rate_limiter.record_request(client_ip, agent_id)

    # 2) Session gate — short-lived ticket preferred; legacy session token ok.
    claims, err = await _claims_from_ws_query(websocket, agent_id)
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

    # Bridge: ws_reader pushes inbound audio onto a bounded queue;
    # stream_transcribe pulls from it and pushes transcripts back via on_event.
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_AUDIO_QUEUE)
    bytes_received = 0
    overflow = False

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
        nonlocal bytes_received, overflow
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    bytes_received += len(data)
                    if bytes_received > _MAX_AUDIO_BYTES:
                        overflow = True
                        break
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        overflow = True
                        break
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
            # Ensure the consumer unblocks even if the queue is full (overflow).
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

    reader_task = asyncio.create_task(ws_reader())
    try:
        await stt.stream_transcribe(audio_iter(), on_event)
        if overflow:
            await on_event({"type": "error", "message": "stt_stream_overload"})
    except Exception as exc:  # provider error must not leak a 500 on the socket
        logger.warning("stt stream transcribe error: %s", exc)
        await on_event({"type": "error", "message": "stt_stream_failed"})
    finally:
        reader_task.cancel()
        with contextlib.suppress(Exception):
            await reader_task
        with contextlib.suppress(Exception):
            if overflow:
                await websocket.close(code=_WS_OVERLOAD)
            else:
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
