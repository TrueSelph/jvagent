"""Public agent-profile endpoint for the embeddable messenger.

``GET /agents/{agent_id}/profile`` returns the agent's public branding — avatar
(data URI, from a loaded ``AvatarAction``), display name, and description — so a
customer embed can show the agent's real identity without hardcoding it. Public
(``auth=False``, no session token): this is public branding shown before any
conversation exists, served read-only. The ``AvatarAction`` is resolved by MRO
class name so this module never imports the avatar package.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from jvspatial.api import endpoint

from jvagent.action.interact.public_gate import resolve_agent_action
from jvagent.action.interact.rate_limiter import extract_client_ip, get_rate_limiter

logger = logging.getLogger(__name__)


def _first_str(obj: Any, *names: str) -> Optional[str]:
    for n in names:
        val = getattr(obj, n, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


@endpoint(
    "/agents/{agent_id}/profile",
    methods=["GET"],
    auth=False,
    tags=["Agent"],
)
async def agent_profile_endpoint(request: Request, agent_id: str) -> Any:
    """Return the agent's public profile: {avatar, name, description}."""
    rate_limiter = get_rate_limiter()
    client_ip = extract_client_ip(request) or "unknown"
    try:
        await rate_limiter.record_request(client_ip, agent_id)
    except Exception:  # pragma: no cover - defensive
        pass

    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    avatar: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    if agent is not None:
        name = _first_str(agent, "alias", "name")
        description = _first_str(agent, "description")
        action = await resolve_agent_action(agent, "AvatarAction")
        if action is not None:
            try:
                getter = getattr(action, "get_avatar", None)
                if callable(getter):
                    avatar = getter(with_prefix=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("profile endpoint: get_avatar failed: %s", exc)

    return {"avatar": avatar, "name": name, "description": description}
