"""Action-scoped download capabilities; no generic storage HTTP surface."""

from typing import Any

from fastapi import HTTPException
from jvspatial.api import endpoint

from .file_interface_action import FileInterfaceAction
from .sharing import download_file


@endpoint(
    "/actions/{action_id}/downloads/{code}",
    methods=["GET"],
    auth=False,
    tags=["FileInterfaceAction"],
)
async def download_artifact(action_id: str, code: str) -> Any:
    """The expiring code is the download credential; callers need no login JWT."""
    action = await FileInterfaceAction.get(action_id)
    if not isinstance(action, FileInterfaceAction):
        raise HTTPException(404, "Artifact unavailable")
    return await download_file(action, code)
