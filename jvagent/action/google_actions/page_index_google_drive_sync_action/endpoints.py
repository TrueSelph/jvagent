import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from pydantic import Field

from .page_index_google_drive_sync_action import PageIndexGoogleDriveSyncAction

logger = logging.getLogger(__name__)


async def _get_page_index_google_drive_sync_action(
    action_id: str,
) -> PageIndexGoogleDriveSyncAction:
    """Fetch and validate PageIndexGoogleDriveSyncAction by ID.

    Raises ResourceNotFoundError on not found, ValidationError on wrong type.
    """
    action = await PageIndexGoogleDriveSyncAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=(
                f"PageIndexGoogleDriveSyncAction with ID " f"'{action_id}' not found"
            ),
            details={"action_id": action_id},
        )
    if not isinstance(action, PageIndexGoogleDriveSyncAction):
        raise ValidationError(
            message=(
                f"Action '{action_id}' is not a " f"PageIndexGoogleDriveSyncAction"
            ),
            details={"action_id": action_id},
        )
    return action


@endpoint(
    "/actions/{action_id}/ingest_google_documents",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex Google Drive Sync"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Import result message",
            ),
            "result": ResponseField(
                field_type=dict,
                description="Ingestion results",
            ),
        }
    ),
)
async def ingest_google_documents_endpoint(
    action_id: str,
    # google_drive_folders: List[Dict[str, Any]],
    google_drive_folders: Optional[List[Dict[str, Any]]] = None,
    remove_deleted_documents: bool = Field(
        default=False,
        examples=[False],
        description="Remove documents that are no longer in Google Drive",
    ),
) -> Dict[str, Any]:
    """Recursively extract and ingest PDF documents from Google Drive folders.


    **Args:**

    - action_id: ID of the PageIndexGoogleDriveSyncAction
    - google_drive_folders: List of folder configs, e.g.
      `[{"folder_id": "<id>", "metadata": {"key": "value"}}]`
    - remove_deleted_documents: If True, removes documents no longer present in
      Google Drive


    **Returns:**

    Dictionary with ingestion status and results


    **Raises:**

    - ResourceNotFoundError: If action not found
    - ValidationError: If ingestion fails or integration is unavailable
    """
    action = await _get_page_index_google_drive_sync_action(action_id)

    try:
        result = await action.ingest_documents_from_google_drive(
            google_drive_folders=google_drive_folders,
            remove_deleted_documents=remove_deleted_documents,
        )

        return {
            "message": result.get("message", "Documents ingested successfully"),
            "result": result.get("documents_ingested", {}),
        }
    except Exception as e:
        logger.error(f"Error ingesting Google Drive documents: {e}")
        raise ValidationError(
            message=f"Ingestion failed: {str(e)}",
            details={"error": str(e)},
        )
