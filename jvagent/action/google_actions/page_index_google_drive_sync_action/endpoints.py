import logging
from typing import Any, Dict, List, Optional
from jvagent.core.agent import Agent

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from pydantic import Field
from fastapi import HTTPException, Request
from jvspatial.exceptions import DatabaseError, ValidationError
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.async_utils import create_background_task
from jvspatial.config import use_background_processing
from .page_index_google_drive_sync_action import PageIndexGoogleDriveSyncAction
from jvspatial.api.decorators import EndpointField

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
    retry_failed_documents: bool = Field(
        default=False,
        examples=[False],
        description="Retry failed documents",
    ),
) -> Dict[str, Any]:
    """Recursively extract and ingest PDF documents from Google Drive folders.


    **Args:**

    - action_id: ID of the PageIndexGoogleDriveSyncAction
    - google_drive_folders: List of folder configs, e.g.
      `[{"folder_id": "<id>", "metadata": {"key": "value"}}]`
    - remove_deleted_documents: If True, removes documents no longer present in
    - retry_failed_documents: If True, retries failed documents
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
            retry_failed_documents=retry_failed_documents,
        )

        response = result.get("message") or "No pending documents to ingest"

        return {
            "message": response,
            "result": result.get("documents_ingested", {}),
        }
    except Exception as e:
        logger.error(f"Error ingesting Google Drive documents: {e}")
        raise ValidationError(
            message=f"Ingestion failed: {str(e)}",
            details={"error": str(e)},
        )






@endpoint(
    "/actions/{action_id}/list_google_documents",
    methods=["GET"],
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
async def list_google_documents_endpoint(
    action_id: str,
) -> Dict[str, Any]:
    """List Google Drive documents."""
    action = await _get_page_index_google_drive_sync_action(action_id)
    try:
        result = await action.get_google_drive_documents()
        return {
            "message": "Google Drive documents listed successfully",
            "result": {"documents": result},
        }
    except Exception as e:
        logger.error(f"Error listing Google Drive documents: {e}")
        raise ValidationError(
            message=f"Listing failed: {str(e)}",
            details={"error": str(e)},
        )




@endpoint(
    "/actions/{action_id}/delete_google_documents",
    methods=["GET"],
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
async def delete_google_documents_endpoint(
    action_id: str,
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete Google Drive documents."""
    action = await _get_page_index_google_drive_sync_action(action_id)
    try:
        result = await action.delete_google_drive_documents(document_id=document_id)
        return {
            "message": "Google Drive documents deleted successfully",
            "result": {"documents": result},
        }
    except Exception as e:
        logger.error(f"Error deleting Google Drive documents: {e}")
        raise ValidationError(
            message=f"Deletion failed: {str(e)}",
            details={"error": str(e)},
        )


@endpoint(
    "/page_index_google_drive_sync/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    webhook_auth="api_key",  # Validates API key from query param or header
    tags=["PageIndex Google Drive Sync"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(
                field_type=Optional[str], example="Hello!", default=None
            ),
            "result": ResponseField(
                field_type=dict,
                description="Ingestion results",
            )
        }
    ),
)
async def page_index_google_drive_sync_action_interact(request: Request, agent_id: str) -> Dict[str, Any]:
    """PageIndex Google Drive Sync Action Interact Webhook.

    Processes incoming PageIndex Google Drive Sync Action messages and triggers an interaction via InteractWalker.

    AWS Lambda compatibility: By default, the webhook awaits the full interaction
    (including response generation and WhatsApp send) before returning the HTTP response.
    This ensures the interaction completes before Lambda freezes the execution context.

    Set BACKGROUND_PROCESSING=true to use background task mode (for long-running servers).

    Args:
        request: FastAPI request object
        agent_id: Agent ID from URL path

    Returns:
        Dict containing status and optional response message

    Raises:
        ResourceNotFoundError: If agent or action not found
        HTTPException: For validation errors
    """
    logger = logging.getLogger(__name__)
    try:
        # Validate agent exists
        agent = await Agent.get(agent_id)
        if not agent:
            raise ResourceNotFoundError(
                message=f"Agent with ID '{agent_id}' not found",
                details={"agent_id": agent_id},
            )

        page_index_google_drive_sync_action = await agent.get_action_by_type("PageIndexGoogleDriveSyncAction")
        if not page_index_google_drive_sync_action:
            raise ResourceNotFoundError(
                message="Action with label 'PageIndexGoogleDriveSyncAction' not found",
                details={"agent_id": agent_id},
            )
        # Parse request data with error handling
        # Use webhook middleware's parsed payload when available (body may be consumed)
        request_data = getattr(request.state, "parsed_payload", None)
        if request_data is None:
            try:
                body = await request.body()
                if body:
                    request_data = await request.json()
                else:
                    request_data = {}
            except Exception:
                request_data = {}

        if use_background_processing():
            # Async mode: Return immediately with 200 OK and process in background
            logger.info(f"Processing ingestion asynchronously for agent {agent_id}")
            create_background_task(
                page_index_google_drive_sync_action.ingest_documents_from_google_drive(
                    google_drive_folders=request_data.get("google_drive_folders"),
                    remove_deleted_documents=request_data.get("remove_deleted_documents"),
                    retry_failed_documents=request_data.get("retry_failed_documents"),
                ),
                name=f"page_index_ingestion_{agent_id}",
            )
            return {
                "status": "received",
                "response": "Ingestion started in background",
                "result": {},
            }
        else:
            logger.info(f"Processing ingestion synchronously for agent {agent_id}")
            # Sync mode (default): Await full interaction before returning
            result = await page_index_google_drive_sync_action.ingest_documents_from_google_drive(
                google_drive_folders=request_data.get("google_drive_folders"),
                remove_deleted_documents=request_data.get("remove_deleted_documents"),
                retry_failed_documents=request_data.get("retry_failed_documents"),
            )
            response = result.get("message") or "No pending documents to ingest"
            return {
                "status": "received",
                "response": response,
                "result": result.get("documents_ingested", {}),
            }
    except (ResourceNotFoundError, HTTPException):
        raise
    except DatabaseError as e:
        logger.error(f"Database error in PageIndex Google Drive Sync Action Interact Webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in PageIndex Google Drive Sync Action Interact Webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")