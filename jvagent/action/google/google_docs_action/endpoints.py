"""API endpoints for Google Docs action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .google_docs_action import GoogleDocsAction

logger = logging.getLogger(__name__)


async def _require_google_docs_action(action_id: str) -> GoogleDocsAction:
    return await require_typed_action(
        action_id,
        GoogleDocsAction,
        not_found_message=f"Google Docs action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleDocsAction",
    )


@endpoint(
    "/actions/{action_id}/docs/create",
    methods=["POST"],
    auth=True,
    tags=["Google Docs Action"],
    summary="Create a new Google Doc",
)
async def docs_create(
    action_id: str,
    title: str,
) -> Dict[str, Any]:
    action = await _require_google_docs_action(action_id)
    result = await action.create_document(title=title)
    return {"success": True, "document": result}


@endpoint(
    "/actions/{action_id}/docs/read",
    methods=["GET"],
    auth=True,
    tags=["Google Docs Action"],
    summary="Read a Google Doc's content",
)
async def docs_read(
    action_id: str,
    document_id: str,
) -> Dict[str, Any]:
    action = await _require_google_docs_action(action_id)
    content = await action.read_document(document_id=document_id)
    return {"success": True, "document": content}


@endpoint(
    "/actions/{action_id}/docs/append",
    methods=["POST"],
    auth=True,
    tags=["Google Docs Action"],
    summary="Append text to a Google Doc",
)
async def docs_append(
    action_id: str,
    document_id: str,
    text: str,
) -> Dict[str, Any]:
    action = await _require_google_docs_action(action_id)
    result = await action.append_text(document_id=document_id, text=text)
    return {"success": True, "appended": result}


@endpoint(
    "/actions/{action_id}/docs/batch-update",
    methods=["POST"],
    auth=True,
    tags=["Google Docs Action"],
    summary="Batch update a Google Doc",
)
async def docs_batch_update(
    action_id: str,
    document_id: str,
    requests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    action = await _require_google_docs_action(action_id)
    result = await action.batch_update(document_id=document_id, requests=requests)
    return {"success": True, "updated": result}
