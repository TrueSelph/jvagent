"""API endpoints for Google Drive action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .google_drive_action import GoogleDriveAction

logger = logging.getLogger(__name__)


async def _get_drive_action(action_id: str) -> Optional[GoogleDriveAction]:
    """Resolve action by ID; validate it is a GoogleDriveAction.

    **Args:**

    - action_id: ID of the action to retrieve

    **Returns:**

    GoogleDriveAction instance if found and valid, else None
    """
    action = await GoogleDriveAction.get(action_id)
    if action and isinstance(action, GoogleDriveAction):
        return action
    return None


@endpoint(
    "/actions/{action_id}/google_drive/upload",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
    summary="Upload a file to Google Drive",
    response=success_response(
        data={
            "file": ResponseField(
                field_type=Dict[str, Any],
                description="Uploaded file information containing id, name, and mimeType",
                example={
                    "id": "1abc2def3ghi4jkl5mno6pqr7stu8vwx9yz",  # pragma: allowlist secret
                    "name": "report.pdf",
                    "mimeType": "application/pdf",
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
async def upload_to_drive(
    action_id: str,
    name: str,
    content: Optional[str] = None,
    source_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a file to Google Drive.

    **Overview:**

    Uploads a file to a specified folder in Google Drive. You can provide the file content
    as a base64 encoded string or a source URL to download the file from.

    **Notes:**

    - If both `content` and `source_url` are provided, `content` takes precedence.
    - If neither is provided, a folder will be created instead.
    - The `parent_folder_id` defaults to the root folder or the action's configured default.

    **Args:**

    - action_id: ID of the Google Drive action
    - name: Name of the file or folder to create
    - content: Optional base64 encoded file content
    - source_url: Optional URL to download file content from
    - mime_type: Optional MIME type for the file
    - parent_folder_id: Optional folder ID where the file should be uploaded

    **Returns:**

    Dictionary containing:
    - **file**: Meta-information about the uploaded file
    - **success**: Always True if the request completes

    **Raises:**

    - ResourceNotFoundError: If the Google Drive action is not found
    - ValidationError: If the upload operation fails
    """
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Drive action {action_id} not found",
            details={"action_id": action_id},
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
        logger.error(f"Failed to upload file to drive: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to upload file: {str(e)}",
            details={"action_id": action_id, "file_name": name},
        )


@endpoint(
    "/actions/{action_id}/google_drive/list",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
    summary="List files in a Google Drive folder",
    response=success_response(
        data={
            "files": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of files and folders in the target location",
                example=[
                    {
                        "id": "1abc2def3ghi4jkl5mno6pqr7stu8vwx9yz",  # pragma: allowlist secret
                        "name": "report.pdf",
                        "mimeType": "application/pdf",
                        "url": "https://drive.google.com/file/d/.../view?usp=sharing",
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
async def list_drive_files(
    action_id: str, folder_id: Optional[str] = None, with_link: bool = False
) -> Dict[str, Any]:
    """List files in Google Drive.

    **Overview:**

    Retrieves a list of files and folders from a specific location in Google Drive.

    **Args:**

    - action_id: ID of the Google Drive action
    - folder_id: Optional folder ID to list files from (defaults to root)
    - with_link: Optional. If True, includes a sharing URL for each file. default=False

    **Returns:**

    Dictionary containing:
    - **files**: List of file objects (id, name, mimeType, and optionally url)
    - **success**: Always True if retrieval completes

    **Raises:**

    - ResourceNotFoundError: If the Google Drive action is not found
    """
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Drive action {action_id} not found",
            details={"action_id": action_id},
        )

    files = await action.list_files(folder_id=folder_id, with_link=with_link)
    return {"success": True, "files": files}


@endpoint(
    "/actions/{action_id}/google_drive/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
    summary="Delete a file from Google Drive",
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
async def delete_drive_file(action_id: str, file_id: str) -> Dict[str, Any]:
    """Delete a file from Google Drive.

    **Overview:**

    Permanently deletes a file from Google Drive by its ID.

    **Args:**

    - action_id: ID of the Google Drive action
    - file_id: Unique ID of the file to delete

    **Returns:**

    Dictionary containing:
    - **success**: True if the deletion was successful

    **Raises:**

    - ResourceNotFoundError: If the Google Drive action is not found
    - ValidationError: If the deletion operation fails
    """
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Drive action {action_id} not found",
            details={"action_id": action_id},
        )

    try:
        success = await action.delete_file(file_id=file_id)
        return {"success": success}
    except Exception as e:
        logger.error(f"Failed to delete file from drive: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to delete file: {str(e)}",
            details={"action_id": action_id, "file_id": file_id},
        )


@endpoint(
    "/actions/{action_id}/google_drive/share",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Drive Action"],
    summary="Share a Google Drive file",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Result of the sharing operation, may include webViewLink",
                example={
                    "webViewLink": "https://drive.google.com/file/d/.../view?usp=sharing"
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the sharing operation was successful",
                example=True,
            ),
        }
    ),
)
async def share_drive_file(
    action_id: str,
    file_id: str,
    share_type: str = "link",
    link_scope: str = "anyone",
    email: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Share a file on Google Drive.

    **Overview:**

    Configures sharing permissions for a file. Supports public link sharing
    or sharing with a specific email address.

    **Scenarios:**

    - **Link Sharing**: Set `share_type="link"` and optional `link_scope`.
    - **Email Sharing**: Set `share_type="user"` and provide target `email`.

    **Args:**

    - action_id: ID of the Google Drive action
    - file_id: ID of the file to share
    - share_type: Type of share ("link" or "user"). default="link"
    - link_scope: Visibility if share_type is "link" ("anyone" or "domain"). default="anyone"
    - email: Target email address if share_type is "user"
    - role: Permission level ("reader", "commenter", "writer"). default="reader"

    **Returns:**

    Dictionary containing:
    - **result**: Information about the share (e.g., link)
    - **success**: Always True if sharing is successful

    **Raises:**

    - ResourceNotFoundError: If the Google Drive action is not found
    - ValidationError: If sharing parameters are invalid or operation fails
    """
    action = await _get_drive_action(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Google Drive action {action_id} not found",
            details={"action_id": action_id},
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
        logger.error(f"Failed to share file on drive: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to share file: {str(e)}",
            details={"action_id": action_id, "file_id": file_id},
        )
