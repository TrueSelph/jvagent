from typing import Any, Dict, Optional

from jvspatial.api import endpoint

from jvagent.action.base import Action

from .postiz_action import PostizAction


async def _resolve_action(action_id: str) -> Optional[PostizAction]:
    """Resolve a PostizAction by its exact node id.

    Scoping by action_id (instead of the previous
    ``find_one({"context.enabled": True})``) avoids the cross-tenant leak
    where the first enabled PostizAction across all agents was returned.
    See AUDIT-actions XC-12.
    """
    node = await Action.get(action_id)
    if not isinstance(node, PostizAction) or not node.enabled:
        return None
    return node


@endpoint(
    "/actions/{action_id}/postiz/auth/{provider}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    operation_id="get_postiz_auth_url",
    tags=["Postiz"],
)
async def get_postiz_auth_url(action_id: str, provider: str) -> Dict[str, Any]:
    """Get the Postiz OAuth URL for a specific social media provider.

    Args:
        action_id: ID of the PostizAction node (per-agent scope).
        provider: Social media platform identifier.

    Returns the URL the user must visit to complete the OAuth consent.
    """
    action = await _resolve_action(action_id)
    if not action:
        return {"error": "PostizAction not found or disabled"}

    try:
        url = await action.get_auth_url(provider)
        return {"url": url}
    except Exception as e:
        return {"error": str(e)}


@endpoint(
    "/actions/{action_id}/postiz/providers",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    operation_id="get_postiz_providers",
    tags=["Postiz"],
)
async def get_postiz_providers(action_id: str) -> Dict[str, Any]:
    """Get the social media providers supported by this PostizAction.

    Args:
        action_id: ID of the PostizAction node (per-agent scope).
    """
    action = await _resolve_action(action_id)
    if not action:
        return {"error": "PostizAction not found or disabled"}

    try:
        providers = await action.list_available_providers()
        return {"providers": providers}
    except Exception as e:
        return {"error": str(e)}
