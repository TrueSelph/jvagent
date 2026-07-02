"""REST endpoints for ClientPushAction.

Provides:
- ``POST /agents/{agent_id}/client-push`` — publish a message to a session.
- ``POST /agents/{agent_id}/client-push/subscribe`` — subscribe to messages
  for a session. Pass ``stream: true`` for SSE (long-lived push), or
  ``stream: false`` for a one-shot drain of queued messages.
"""


import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from jvagent.action.response.streaming import (
    create_sse_response,
    format_sse_chunk,
    stream_messages,
)
from jvagent.core.agent import Agent
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .client_push_action import ClientPushAction

logger = logging.getLogger(__name__)


async def _get_action(agent_id: str) -> ClientPushAction:
    """Load the agent and return its configured ``ClientPushAction``.

    Raises:
        ResourceNotFoundError: If the agent or action is missing.
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action: ClientPushAction = await agent.get_action_by_type("ClientPushAction")
    if not action:
        raise ResourceNotFoundError(
            message="ClientPushAction not found on agent",
            details={"agent_id": agent_id},
        )
    return action


def _authenticate(request: Request, agent_id: str) -> str:
    """Verify ``Authorization: Bearer`` or ``x-session-token`` header.

    Returns the verified ``user_id`` on success. Raises ``AuthenticationError``
    when no header or an invalid token is supplied.
    """
    from jvagent.action.interact.session_token import (
        verify_bearer,
        verify_session_token,
    )

    auth_header = request.headers.get("authorization") or ""
    parts = auth_header.split(None, 1)
    bearer_token = (
        parts[1].strip()
        if len(parts) == 2 and parts[0].lower() == "bearer"
        else None
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

    if not uid:
        from jvspatial.api.exceptions import AuthenticationError

        raise AuthenticationError(
            message="Authentication required — send Authorization: Bearer <token> or x-session-token header",
            details={"agent_id": agent_id},
        )

    return uid


# ── Publish ──────────────────────────────────────────────────────────────────


@endpoint(
    "/agents/{agent_id}/client-push",
    methods=["POST"],
    auth=True,
    tags=["Client Push"],
)
async def client_push_endpoint(
    agent_id: str,
    message: str = "Hello from ClientPushAction!",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a message to a client session.

    Invokes ``ClientPushAction`` to publish the message directly to the given
    session via the agent's ``ResponseBus``.

    Args:
        agent_id:  The agent whose ``ClientPushAction`` to invoke.
        message:   The message text to deliver.
        user_id:   User identifier for adapter routing.
        session_id: Target session to deliver the message to.

    Returns:
        Delivery status: ``{"ok": true, "delivery": "response_bus", …}``.

    Example:
        .. code-block:: bash

            curl -X POST \\
              'http://localhost:8000/api/agents/jvagent_orchestrator/client-push' \\
              -H 'Content-Type: application/json' \\
              -d '{
                "message": "Hello, client!",
                "user_id": "user_abc123",
                "session_id": "session_xyz789"
              }'
    """
    logger.info(
        "client_push: agent=%s user=%s session=%s msg=%s",
        agent_id,
        user_id,
        session_id,
        message,
    )
    action = await _get_action(agent_id)
    return await action.send_message(message, user_id=user_id, session_id=session_id)


# ── Subscribe ────────────────────────────────────────────────────────────────


@endpoint(
    "/agents/{agent_id}/client-push/subscribe",
    methods=["POST"],
    tags=["Client Push"],
)
async def client_push_subscribe(
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
    # Authenticate before serving any data
    uid = _authenticate(request, agent_id)

    logger.info(
        "client_push/subscribe: agent=%s session=%s stream=%s user=%s",
        agent_id,
        session_id,
        stream,
        uid,
    )

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
