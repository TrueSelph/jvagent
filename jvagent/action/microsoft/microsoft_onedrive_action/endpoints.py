"""API endpoints for Microsoft OneDrive action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .microsoft_onedrive_action import MicrosoftOneDriveAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/upload",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft OneDrive Action"],
    summary="Upload a file to OneDrive",
    response=success_response(
        data={
            "file": ResponseField(
                field_type=Dict[str, Any],
                description="Uploaded file or folder metadata from Microsoft Graph",
                example={
                    "id": "01BYE5RZODCPIJQA6SXZA5BWRKBYZHKAXL",
                    "name": "report.pdf",
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the upload was successful",
                example=True,
            ),
        }
    ),
)
async def upload_to_onedrive(
    action_id: str,
    name: str,
    content: Optional[str] = None,
    source_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a file (or create a folder) in OneDrive via Microsoft Graph."""
    action = await require_typed_action(
        action_id,
        MicrosoftOneDriveAction,
        not_found_message=f"Microsoft OneDrive action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOneDriveAction",
    )

    try:
        result = await action.upload_file(
            name=name,
            content=content,
            source_url=source_url,
            mime_type=mime_type,
            parent_folder_id=parent_folder_id,
        )
        return {"success": True, "file": result}
    except Exception as e:
        logger.error("Failed to upload file to OneDrive: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to upload file: {e}",
            details={"action_id": action_id, "file_name": name},
        )


@endpoint(
    "/actions/{action_id}/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft OneDrive Action"],
    summary="List files in a OneDrive folder",
    response=success_response(
        data={
            "files": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of drive items",
                example=[
                    {
                        "id": "01BYE5RZODCPIJQA6SXZA5BWRKBYZHKAXL",  # pragma: allowlist secret
                        "name": "report.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the retrieval was successful",
                example=True,
            ),
        }
    ),
)
async def list_onedrive_files(
    action_id: str, folder_id: Optional[str] = None, with_link: bool = False
) -> Dict[str, Any]:
    """List children in a OneDrive folder (or root)."""
    action = await require_typed_action(
        action_id,
        MicrosoftOneDriveAction,
        not_found_message=f"Microsoft OneDrive action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOneDriveAction",
    )

    files = await action.list_files(folder_id=folder_id, with_link=with_link)
    return {"success": True, "files": files}


@endpoint(
    "/actions/{action_id}/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft OneDrive Action"],
    summary="Delete a file from OneDrive",
    response=success_response(
        data={
            "success": ResponseField(
                field_type=bool,
                description="Whether the deletion was successful",
                example=True,
            ),
        }
    ),
)
async def delete_onedrive_file(action_id: str, file_id: str) -> Dict[str, Any]:
    """Permanently delete a drive item by id."""
    action = await require_typed_action(
        action_id,
        MicrosoftOneDriveAction,
        not_found_message=f"Microsoft OneDrive action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOneDriveAction",
    )

    try:
        success = await action.delete_file(file_id=file_id)
        return {"success": success}
    except Exception as e:
        logger.error("Failed to delete OneDrive file: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to delete file: {e}",
            details={"action_id": action_id, "file_id": file_id},
        )


@endpoint(
    "/actions/{action_id}/share",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft OneDrive Action"],
    summary="Share a OneDrive file",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Result of the sharing operation",
                example={"webViewLink": "https://contoso.sharepoint.com/..."},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the sharing operation was successful",
                example=True,
            ),
        }
    ),
)
async def share_onedrive_file(
    action_id: str,
    file_id: str,
    share_type: str = "link",
    link_scope: str = "anyone",
    email: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Configure sharing for a OneDrive item."""
    action = await require_typed_action(
        action_id,
        MicrosoftOneDriveAction,
        not_found_message=f"Microsoft OneDrive action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOneDriveAction",
    )

    if share_type == "user" and not email:
        raise ValidationError(
            message="Email is required for 'user' share type",
            details={"share_type": share_type},
        )

    try:
        result = await action.share_file(
            file_id=file_id,
            share_type=share_type,
            link_scope=link_scope,
            email=email,
            role=role,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error("Failed to share OneDrive file: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to share file: {e}",
            details={"action_id": action_id, "file_id": file_id},
        )


@endpoint(
    "/actions/{action_id}/compare_files",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft OneDrive Action"],
    summary="Compare two file listings from OneDrive",
    response=success_response(
        data={
            "added": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Items added between the two listings",
                example=[],
            ),
            "removed": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Items removed between the two listings",
                example=[],
            ),
            "modified": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Items modified between the two listings",
                example=[],
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the comparison succeeded",
                example=True,
            ),
        }
    ),
)
async def compare_onedrive_files(
    action_id: str, old_files: List[Dict], new_files: List[Dict]
) -> Dict[str, Any]:
    """Compare two nested file listings (added / removed / modified)."""
    action = await require_typed_action(
        action_id,
        MicrosoftOneDriveAction,
        not_found_message=f"Microsoft OneDrive action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftOneDriveAction",
    )

    diff = action.compare_files(old_files=old_files, new_files=new_files)
    return {"success": True, **diff}
