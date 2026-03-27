from typing import Any, Dict
from jvspatial.api import endpoint
from .postiz_action import PostizAction

@endpoint(
    "/postiz/auth/{provider}",
    methods=["GET"],
    auth=True,
    operation_id="get_postiz_auth_url",
    tags=["Postiz"]
)
async def get_postiz_auth_url(provider: str) -> Dict[str, Any]:
    """Get the Postiz OAuth URL for a specific social media provider.
    
    This endpoint initiates the programmatic authentication flow. It returns
    a URL that the user must visit to complete the OAuth consent process.
    """
    action = await PostizAction.find_one({"context.enabled": True})
    if not action:
        return {"error": "PostizAction not found or disabled"}
    
    try:
        url = await action.get_auth_url(provider)
        return {"url": url}
    except Exception as e:
        return {"error": str(e)}

@endpoint(
    "/postiz/providers",
    methods=["GET"],
    auth=True,
    operation_id="get_postiz_providers",
    tags=["Postiz"]
)
async def get_postiz_providers() -> Dict[str, Any]:
    """Get a list of all social media providers supported by the Postiz instance.

    This returns all platforms compatible with Postiz (e.g., x, linkedin, facebook),
    allowing for an informed selection before initiating the authentication flow.
    """
    action = await PostizAction.find_one({"context.enabled": True})
    if not action:
        return {"error": "PostizAction not found or disabled"}

    try:
        providers = await action.list_available_providers()
        return {"providers": providers}
    except Exception as e:
        return {"error": str(e)}
