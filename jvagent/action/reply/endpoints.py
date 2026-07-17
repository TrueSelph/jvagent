"""REST endpoints for the ReplyAction publish+subscribe surface.

Provides:
- ``POST /agents/{agent_id}/reply/publish`` — publish a message to a session.
- ``POST /agents/{agent_id}/reply/subscribe`` — subscribe to messages for a
  session. Pass ``stream: true`` for SSE (long-lived push), or
  ``stream: false`` for a one-shot poll.
"""

import logging
from typing import Any, Optional

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.response.streaming import (
    create_sse_response,
    stream_messages,
)
from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)


async def _get_agent_and_bus(agent_id: str) -> tuple[Agent, ResponseBus]:
    """Load the agent and return ``(agent, response_bus)``.

    Raises:
        ResourceNotFoundError: If the agent is missing or has no bus.
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    bus = await agent.get_response_bus()
    if bus is None:
        raise ResourceNotFoundError(
            message="ResponseBus not available on agent",
            details={"agent_id": agent_id},
        )
    return agent, bus


def _authenticate(request: Request, agent_id: str) -> tuple[str, Optional[str]]:
    """Verify ``Authorization: Bearer`` or ``x-session-token`` header.

    Returns ``(user_id, bound_session_id)`` on success:

    - ``user_id`` — the verified caller identity.
    - ``bound_session_id`` — for a Mode B session capability token, the
      ``session_id`` the token is bound to (the token is a capability for
      exactly that session). ``None`` for a Mode A login bearer, which is
      not session-scoped and must be authorized against conversation
      ownership instead.

    Raises ``AuthenticationError`` when no header or an invalid token is
    supplied.
    """
    from jvagent.action.interact.session_token import (
        verify_bearer,
        verify_session_token,
    )

    auth_header = request.headers.get("authorization") or ""
    parts = auth_header.split(None, 1)
    bearer_token = (
        parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
    )

    uid: Optional[str] = None
    if bearer_token:
        uid = verify_bearer(bearer_token)

    if not uid:
        stoken = request.headers.get("x-session-token", "") or ""
        if stoken:
            claims, _ = verify_session_token(stoken, expected_agent_id=agent_id)
            if claims:
                uid = claims.get("user_id")
                if uid:
                    # Mode B token: bound to a specific session. Return the
                    # bound session so the caller can enforce that the
                    # requested session matches — the token is not a
                    # blanket credential for the whole agent.
                    return uid, claims.get("session_id")

    if not uid:
        from jvspatial.api.exceptions import AuthenticationError

        raise AuthenticationError(
            message="Authentication required — send Authorization: Bearer <token> or x-session-token header",
            details={"agent_id": agent_id},
        )

    return uid, None


async def _authorize_session(
    agent: Agent,
    uid: str,
    bound_session_id: Optional[str],
    requested_session_id: str,
) -> None:
    """Enforce that ``uid`` may access ``requested_session_id`` on ``agent``.

    Without this check the reply publish/subscribe surface is an IDOR: an
    authenticated caller could read (and, on the one-shot poll, drain) any
    session's messages, or inject agent-attributed content into any session.

    Two identity kinds:

    - **Mode B session token** (``bound_session_id`` set): the token is a
      capability for exactly one session — the requested session must equal
      the bound one. No DB lookup needed.
    - **Mode A login bearer** (``bound_session_id`` is ``None``): authorize
      against conversation ownership — the conversation for the requested
      session must exist under this agent and belong to ``uid``.

    Raises ``AuthorizationError`` (403) when access is not permitted.
    """
    from jvspatial.api.exceptions import AuthorizationError

    if bound_session_id is not None:
        if bound_session_id == requested_session_id:
            return
        raise AuthorizationError(
            message="Session token is not valid for the requested session",
            details={"agent_id": agent.id},
        )

    # Mode A: require ownership of the target conversation.
    memory = await agent.get_memory()
    conversation = (
        await memory.get_conversation_by_session(requested_session_id)
        if memory
        else None
    )
    if conversation is not None and getattr(conversation, "user_id", None) == uid:
        return
    raise AuthorizationError(
        message="You do not have access to this session",
        details={"agent_id": agent.id},
    )


# ── Publish ──────────────────────────────────────────────────────────────────


@endpoint(
    "/agents/{agent_id}/reply/publish",
    methods=["POST"],
    tags=["Reply"],
)
async def reply_publish_endpoint(
    request: Request,
    agent_id: str,
    message: str = "Hello from ReplyAction!",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Publish a message to a client session through the agent's ResponseBus.

    Requires authentication (``Authorization: Bearer`` login token or
    ``x-session-token``) AND authorization for the target session — the
    caller must own the conversation (Mode A) or present a session token
    bound to it (Mode B). Without this an authenticated caller could inject
    agent-attributed content into any session on any agent.

    Args:
        agent_id:  The target agent.
        message:   The message text to deliver.
        user_id:   User identifier for adapter routing.
        session_id: Target session to deliver the message to.

    Returns:
        Delivery status.
    """
    uid, bound_session_id = _authenticate(request, agent_id)

    if not session_id:
        from jvspatial.api.exceptions import InvalidInputError

        raise InvalidInputError(
            message="session_id is required",
            details={"agent_id": agent_id},
        )

    agent, bus = await _get_agent_and_bus(agent_id)
    await _authorize_session(agent, uid, bound_session_id, session_id)

    logger.info(
        "reply/publish: agent=%s user=%s session=%s",
        agent_id,
        uid,
        session_id,
    )

    msg = await bus.publish(
        session_id=session_id or "",
        content=message,
        channel="default",
        stream=False,
        user_id=user_id,
        streaming_complete=True,
    )
    return {
        "ok": True,
        "delivery": "response_bus",
        "session_id": session_id,
        "message_id": getattr(msg, "id", None),
    }


# ── Subscribe ────────────────────────────────────────────────────────────────


@endpoint(
    "/agents/{agent_id}/reply/subscribe",
    methods=["POST"],
    tags=["Reply"],
)
async def reply_subscribe_endpoint(
    request: Request,
    agent_id: str,
    session_id: str,
    stream: bool = False,
) -> Any:
    """Subscribe to messages for a session.

    When ``stream=true`` returns a long-lived SSE connection that pushes every
    response-bus message for the session to the client in real time. The
    connection stays open indefinitely — the client should reconnect (standard
    SSE reconnect behaviour) if it drops.

    When ``stream=false`` (default) this is a one-shot poll: any messages
    queued since the last call are drained and returned as JSON. Call on a
    short interval (every 3–5 seconds) to approximate real-time delivery.

    Both modes require authentication via ``Authorization: Bearer <token>``
    (Mode A JWT login token) or ``x-session-token`` (Mode B session capability
    token).

    Args:
        agent_id:   Target agent.
        session_id: Session whose messages to subscribe to.
        stream:     ``true`` for SSE, ``false`` for one-shot poll.

    Returns:
        - ``stream=true`` → ``text/event-stream`` ``StreamingResponse``.
        - ``stream=false`` → ``{"ok": true, "messages": […]}``.
    """
    # Authenticate, then authorize the caller for this specific session
    # before serving any data (prevents cross-session disclosure / drain).
    uid, bound_session_id = _authenticate(request, agent_id)

    logger.info(
        "reply/subscribe: agent=%s session=%s stream=%s user=%s",
        agent_id,
        session_id,
        stream,
        uid,
    )

    agent, bus = await _get_agent_and_bus(agent_id)
    await _authorize_session(agent, uid, bound_session_id, session_id)

    if stream:
        # ── SSE: long-lived push ──
        return create_sse_response(
            stream_messages(session_id, bus),
            headers={"X-Session-ID": session_id},
        )

    # ── One-shot poll: drain and return ──
    messages = bus._session_queues.pop(session_id, [])
    return {
        "ok": True,
        "messages": [m.to_dict() for m in messages],
    }
