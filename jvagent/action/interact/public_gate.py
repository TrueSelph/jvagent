"""Shared gate for the public messenger endpoints (voice + uploads).

These endpoints are ``auth=False`` (anonymous, embeddable) like ``interact``,
but they trigger expensive provider calls (STT/TTS) or storage writes, so — per
the messenger design decision — they **always** require a valid Mode B session
capability token (``X-Session-Token``), regardless of
``JVAGENT_INTERACT_PUBLIC_AUTH`` mode. The token must have been minted by a prior
``/interact`` turn and still bind to its ``Conversation``; this ties every voice
clip and upload to an established conversation and reuses the interact rate
limiter for abuse control.

Because a token is only ever minted when ``JVAGENT_INTERACT_PUBLIC_AUTH`` is
``log`` or ``required`` (and ``JVSPATIAL_JWT_SECRET_KEY`` is set), voice/uploads
are unavailable in ``off`` mode by construction — that is the intended
fail-closed behavior for anonymous provider access.
"""

from __future__ import annotations

import logging
from typing import Any, Tuple

from fastapi import Request
from jvspatial.api.exceptions import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
)

from jvagent.action.interact.rate_limiter import extract_client_ip, get_rate_limiter
from jvagent.action.interact.session_token import (
    claims_match_conversation,
    verify_session_token,
)

logger = logging.getLogger(__name__)


async def _load_conversation(agent: Any, session_id: str) -> Any:
    try:
        memory = await agent.get_memory()
        if memory is None:
            return None
        return await memory.get_conversation_by_session(session_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("public_gate: conversation load failed: %s", exc)
        return None


async def require_messenger_session(
    request: Request, agent_id: str
) -> Tuple[Any, dict]:
    """Authorize a public messenger request against an established conversation.

    Enforces (in order): IP+agent rate limit → a present, signature-valid
    ``X-Session-Token`` for this agent → the agent exists → the token still
    binds to its web-owned ``Conversation``. Returns ``(agent, claims)`` or
    raises the appropriate jvspatial API exception (429 / 401 / 404).
    """
    rate_limiter = get_rate_limiter()
    client_ip = extract_client_ip(request) or "unknown"
    if not await rate_limiter.check_rate_limit(client_ip, agent_id):
        raise RateLimitError(
            message=(
                f"Rate limit exceeded: {rate_limiter.rate_limit_per_minute} "
                "requests per minute"
            ),
            details={"ip": client_ip, "agent_id": agent_id},
        )
    await rate_limiter.record_request(client_ip, agent_id)

    token = request.headers.get("x-session-token")
    token = token.strip() if token else ""
    if not token:
        raise AuthenticationError(
            message=(
                "A session token is required. Start a conversation via the "
                "interact endpoint first, then send its X-Session-Token."
            ),
            details={"reason": "missing_session_token"},
        )

    claims, err = verify_session_token(token, expected_agent_id=agent_id)
    if err or claims is None:
        raise AuthenticationError(
            message="Invalid or expired session token.",
            details={"reason": f"token_{err or 'invalid'}"},
        )

    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    conv = await _load_conversation(agent, str(claims.get("session_id") or ""))
    bind_err = claims_match_conversation(claims, conv)
    if bind_err:
        raise AuthenticationError(
            message="Session token does not authorize this conversation.",
            details={"reason": f"bind_{bind_err}"},
        )
    return agent, claims


async def resolve_agent_action(agent: Any, base_class_name: str) -> Any:
    """Return the agent's first enabled action whose type MRO includes *base_class_name*.

    Matches by class name across the MRO instead of ``isinstance`` so this
    module never imports the provider base classes (``BaseSTTAction`` /
    ``BaseTTSAction``) — importing them eagerly pulls provider SDKs (deepgram,
    elevenlabs) that are optional deps, which would couple interact endpoint
    registration to those SDKs being installed.
    """
    try:
        manager = await agent.get_actions_manager()
        if manager is None:
            return None
        for action in await manager.get_actions(enabled_only=True):
            mro = type(action).__mro__
            if any(getattr(base, "__name__", "") == base_class_name for base in mro):
                return action
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("public_gate: action resolve failed: %s", exc)
    return None
