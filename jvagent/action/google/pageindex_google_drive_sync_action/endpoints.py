import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from jvspatial import create_task, is_serverless_mode
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.exceptions import (
    DatabaseError,
)
from jvspatial.exceptions import ValidationError as SpatialValidationError
from pydantic import Field

from jvagent.action.pageindex.endpoints import _resolve_docling_ocr_for_ingest
from jvagent.action.utils.endpoint_helpers import require_typed_action
from jvagent.core.agent import Agent

from .pageindex_google_drive_sync_action import PageIndexGoogleDriveSyncAction

logger = logging.getLogger(__name__)


def _payload_bool(payload: Dict[str, Any], key: str, *, default: bool) -> bool:
    """Parse JSON/body bool; missing key -> default."""
    if key not in payload:
        return default
    v = payload[key]
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).lower().strip()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return default


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
    convert_to_markdown: bool = Field(
        default=False,
        examples=[False],
        description="Convert PDFs with Docling to Markdown before PageIndex (requires jvagent[pageindex])",
    ),
    ocr: bool = Field(
        default=False,
        examples=[False],
        description="Enable Docling OCR when convert_to_markdown is True (ignored if docling_ocr_engine is set)",
    ),
    docling_ocr_engine: Optional[str] = Field(
        default=None,
        description='Docling OCR: "none" or "rapidocr" (ONNX RapidOCR). Legacy names map to rapidocr. When set, overrides ocr.',
    ),
    normalize_bold_headings: bool = Field(
        default=False,
        examples=[False],
        description=(
            "When True: sparse Markdown bold→## normalization runs on jvforge only "
            "(requires JVAGENT_JVFORGE_BASE_URL)"
        ),
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
            convert_to_markdown=convert_to_markdown,
            ocr=ocr,
            docling_ocr_engine=docling_ocr_engine,
            normalize_bold_headings=normalize_bold_headings,
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
    """Delete Google Drive folder sync state.

    ``document_id`` is the **Google Drive folder id** (not the graph node id).
    Omit to delete all folder nodes for this action.
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
    "/actions/{action_id}/update_google_documents",
    methods=["PATCH"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex Google Drive Sync"],
    summary="Update GoogleDriveDocuments node for a folder",
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Update result message",
            ),
            "result": ResponseField(
                field_type=dict,
                description="Updated GoogleDriveDocuments fields",
            ),
        }
    ),
)
async def update_google_documents_endpoint(
    action_id: str,
    folder_id: str = Field(..., description="Google Drive folder id (GoogleDriveDocuments.folder_id)"),
    folder_name: Optional[str] = Field(
        default=None,
        description="Folder display name stored on the node",
    ),
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Merged into existing node metadata",
    ),
    status: Optional[str] = Field(
        default=None,
        description="Optional explicit status (pending, processing, completed, failed)",
    ),
    ingesting_documents: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Replace ingesting queues (added, modified, removed lists)",
    ),
    failed_documents: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Replace failed queues (added, modified, removed lists)",
    ),
    active_document: Optional[str] = Field(
        default=None,
        description="Current processing label; use empty string to clear",
    ),
) -> Dict[str, Any]:
    """Patch fields on the ``GoogleDriveDocuments`` node for one synced folder."""
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
        result = await action.update_google_drive_documents(
            folder_id,
            folder_name=folder_name,
            metadata=metadata,
            status=status,
            ingesting_documents=ingesting_documents,
            failed_documents=failed_documents,
            active_document=active_document,
        )
        return {
            "message": "Google Drive documents node updated",
            "result": result,
        }
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details=e.details or {},
        )
    except Exception as e:
        logger.error("Error updating Google Drive documents node: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Update failed: {str(e)}",
            details={"error": str(e)},
        )


@endpoint(
    "/actions/{action_id}/set_google_drive_file_ingestion",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex Google Drive Sync"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Update result message",
            ),
            "result": ResponseField(
                field_type=dict,
                description="folder_id, file_id, disable_ingestion",
            ),
        }
    ),
)
async def set_google_drive_file_ingestion_endpoint(
    action_id: str,
    folder_id: str = Field(..., description="Google Drive folder id"),
    file_id: str = Field(..., description="Google Drive file id"),
    disable_ingestion: bool = Field(
        default=False,
        description="When true, skip ingestion for this file",
    ),
) -> Dict[str, Any]:
    """Set per-file ``disable_ingestion`` and remove the file from pending queues when disabling."""
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
        result = await action.set_google_drive_file_ingestion(
            folder_id=folder_id,
            file_id=file_id,
            disable_ingestion=disable_ingestion,
        )
        return {
            "message": "Google Drive file ingestion settings updated",
            "result": result,
        }
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details=e.details or {},
        )
    except Exception as e:
        logger.error("Error updating Google Drive file ingestion: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Update failed: {str(e)}",
            details={"error": str(e)},
        )


@endpoint(
    "/actions/{action_id}/google_drive_file_queue",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex Google Drive Sync"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Queue operation result message",
            ),
            "result": ResponseField(
                field_type=dict,
                description="folder_id, file_id, prioritized_in or cleared",
            ),
        }
    ),
)
async def google_drive_file_queue_endpoint(
    action_id: str,
    folder_id: str = Field(..., description="Google Drive folder id"),
    file_id: str = Field(..., description="Google Drive file id"),
    operation: str = Field(
        ...,
        description='Either "prioritize" (retry next) or "clear" (remove from queues)',
    ),
) -> Dict[str, Any]:
    """Prioritize a file for the next ingest pass, or remove it from ingest/failed queues."""
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
    op = str(operation).strip().lower()
    if op not in ("prioritize", "clear"):
        raise ValidationError(
            message='operation must be "prioritize" or "clear"',
            details={"operation": operation},
        )
    try:
        if op == "prioritize":
            result = await action.prioritize_google_drive_file_for_ingest(
                folder_id=folder_id,
                file_id=file_id,
            )
            return {
                "message": "Google Drive file prioritized for ingest",
                "result": result,
            }
        result = await action.clear_google_drive_file_from_queues(
            folder_id=folder_id,
            file_id=file_id,
        )
        return {
            "message": "Google Drive file removed from ingest queues",
            "result": result,
        }
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details=e.details or {},
        )
    except Exception as e:
        logger.error("Error in google_drive_file_queue: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Queue operation failed: {str(e)}",
            details={"error": str(e)},
        )


@endpoint(
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
        convert_to_markdown = _payload_bool(
            request_data, "convert_to_markdown", default=True
        )
        docling_raw = request_data.get("docling_ocr_engine")
        docling_str = (
            str(docling_raw).strip()
            if docling_raw is not None and str(docling_raw).strip()
            else None
        )
        if docling_str:
            ocr_flag, docling_ocr_eff = _resolve_docling_ocr_for_ingest(docling_str, None)
        else:
            ocr_flag = _payload_bool(request_data, "ocr", default=True)
            docling_ocr_eff = None
        normalize_bold_flag = _payload_bool(
            request_data, "normalize_bold_headings", default=False
        )
        drive_action = pageindex_google_drive_sync_action

        if is_serverless_mode():
            logger.info(
                f"Processing ingestion inline (serverless) for agent {agent_id}"
            )
            result = await drive_action.ingest_documents_from_google_drive(
                google_drive_folders=folders,
                remove_deleted_documents=remove_deleted,
                retry_failed_documents=retry_failed,
                convert_to_markdown=convert_to_markdown,
                ocr=ocr_flag,
                docling_ocr_engine=docling_ocr_eff,
                normalize_bold_headings=normalize_bold_flag,
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
                convert_to_markdown=convert_to_markdown,
                ocr=ocr_flag,
                docling_ocr_engine=docling_ocr_eff,
                normalize_bold_headings=normalize_bold_flag,
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
