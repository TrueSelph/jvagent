import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from jvspatial import create_task, is_serverless_mode
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.exceptions import DatabaseError
from pydantic import Field

from jvagent.action.utils.endpoint_helpers import require_typed_action
from jvagent.core.agent import Agent

from .pageindex_google_drive_sync_action import PageIndexGoogleDriveSyncAction

logger = logging.getLogger(__name__)


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
    action = await require_typed_action(
        action_id,
        PageIndexGoogleDriveSyncAction,
        not_found_message=(
            f"PageIndexGoogleDriveSyncAction with ID '{action_id}' not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a PageIndexGoogleDriveSyncAction"
        ),
    )

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
        logger.error("Error ingesting Google Drive documents: %s", e, exc_info=True)
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
    action = await require_typed_action(
        action_id,
        PageIndexGoogleDriveSyncAction,
        not_found_message=(
            f"PageIndexGoogleDriveSyncAction with ID '{action_id}' not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a PageIndexGoogleDriveSyncAction"
        ),
    )
    try:
        result = await action.get_google_drive_documents()
        return {
            "message": "Google Drive documents listed successfully",
            "result": {"documents": result},
        }
    except Exception as e:
        logger.error("Error listing Google Drive documents: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Listing failed: {str(e)}",
            details={"error": str(e)},
        )


@endpoint(
    "/actions/{action_id}/delete_google_documents",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex Google Drive Sync"],
    summary="Delete Google Drive documents",
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
    action = await require_typed_action(
        action_id,
        PageIndexGoogleDriveSyncAction,
        not_found_message=(
            f"PageIndexGoogleDriveSyncAction with ID '{action_id}' not found"
        ),
        wrong_type_message=(
            f"Action '{action_id}' is not a PageIndexGoogleDriveSyncAction"
        ),
    )
    try:
        result = await action.delete_google_drive_documents(document_id=document_id)
        return {
            "message": "Google Drive documents deleted successfully",
            "result": {"documents": result},
        }
    except Exception as e:
        logger.error("Error deleting Google Drive documents: %s", e, exc_info=True)
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
            ),
        }
    ),
)
async def pageindex_google_drive_sync_action_interact(
    request: Request, agent_id: str
) -> Dict[str, Any]:
    """PageIndex Google Drive Sync Action Interact Webhook.

    Triggers Google Drive document ingestion for the agent's PageIndexGoogleDriveSyncAction.

    When ``is_serverless_mode()`` is true (e.g. ``SERVERLESS_MODE=true`` or Lambda), ingestion
    is awaited before the HTTP response so work completes before the invocation freezes.

    On non-serverless runtimes, ingestion is scheduled with ``create_task`` and the handler
    returns immediately with a short acknowledgement while work runs in the background.

    Args:
        request: FastAPI request object
        agent_id: Agent ID from URL path

    Returns:
        Dict containing status and optional response message

    Raises:
        ResourceNotFoundError: If agent or action not found
        HTTPException: For validation errors
    """
    try:
        # Validate agent exists
        agent = await Agent.get(agent_id)
        if not agent:
            raise ResourceNotFoundError(
                message=f"Agent with ID '{agent_id}' not found",
                details={"agent_id": agent_id},
            )

        pageindex_google_drive_sync_action = await agent.get_action_by_type(
            "PageIndexGoogleDriveSyncAction"
        )
        if not pageindex_google_drive_sync_action:
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

        folders = request_data.get("google_drive_folders")
        remove_deleted = request_data.get("remove_deleted_documents")
        retry_failed = request_data.get("retry_failed_documents")
        drive_action = pageindex_google_drive_sync_action

        if is_serverless_mode():
            logger.info(
                f"Processing ingestion inline (serverless) for agent {agent_id}"
            )
            result = await drive_action.ingest_documents_from_google_drive(
                google_drive_folders=folders,
                remove_deleted_documents=remove_deleted,
                retry_failed_documents=retry_failed,
            )
            response = result.get("message") or "No pending documents to ingest"
            t0 = getattr(request.state, "webhook_start", None)
            if t0 is not None:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                logger.debug(
                    f"PageIndex Drive webhook: ingestion done in {elapsed_ms}ms "
                    f"(serverless)"
                )
            return {
                "status": "received",
                "response": response,
                "result": result.get("documents_ingested", {}),
            }

        task = await create_task(
            drive_action.ingest_documents_from_google_drive(
                google_drive_folders=folders,
                remove_deleted_documents=remove_deleted,
                retry_failed_documents=retry_failed,
            ),
            name=f"page_index_ingestion_{agent_id}",
        )
        if task is None:
            logger.info(f"Processing ingestion synchronously for agent {agent_id}")
        else:
            logger.info(f"Processing ingestion in background for agent {agent_id}")
        t0 = getattr(request.state, "webhook_start", None)
        if t0 is not None:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if task is None:
                logger.debug(
                    f"PageIndex Drive webhook: ingestion done in {elapsed_ms}ms"
                )
            else:
                logger.debug(
                    f"PageIndex Drive webhook: queued for async in {elapsed_ms}ms"
                )
        return {
            "status": "received",
            "response": "Ingestion started in background",
            "result": {},
        }
    except (ResourceNotFoundError, HTTPException):
        raise
    except DatabaseError as e:
        logger.error(
            f"Database error in PageIndex Google Drive Sync Action Interact Webhook: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(
            f"Unexpected error in PageIndex Google Drive Sync Action Interact Webhook: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")
