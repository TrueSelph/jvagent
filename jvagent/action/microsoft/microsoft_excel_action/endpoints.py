"""API endpoints for Microsoft Excel (Graph workbook) action."""

import logging
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .microsoft_excel_action import MicrosoftExcelAction

logger = logging.getLogger(__name__)


def _workbook_ref(
    action: MicrosoftExcelAction,
    spreadsheet_id: Optional[str],
    spreadsheet_url: Optional[str],
) -> str:
    """Resolve workbook target: request params first, then action default."""
    if spreadsheet_url and str(spreadsheet_url).strip():
        return str(spreadsheet_url).strip()
    if spreadsheet_id and str(spreadsheet_id).strip():
        return str(spreadsheet_id).strip()
    if action.spreadsheet_url and str(action.spreadsheet_url).strip():
        return str(action.spreadsheet_url).strip()
    raise ValidationError(
        message=(
            "Provide spreadsheet_url or spreadsheet_id, or set spreadsheet_url "
            "on the MicrosoftExcelAction"
        ),
        details={"spreadsheet_id": spreadsheet_id, "spreadsheet_url": spreadsheet_url},
    )


@endpoint(
    "/actions/{action_id}/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Excel Action"],
    summary="Delete an Excel workbook on OneDrive",
    response=success_response(
        data={
            "success": ResponseField(
                field_type=bool,
                description="Whether the workbook was deleted",
                example=True,
            ),
        }
    ),
)
async def delete_excel_workbook(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Permanently delete the workbook (drive item) via Microsoft Graph."""
    action = await require_typed_action(
        action_id,
        MicrosoftExcelAction,
        not_found_message=f"Microsoft Excel action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftExcelAction",
    )

    ref = _workbook_ref(action, spreadsheet_id, spreadsheet_url)

    try:
        ok = await action.delete_spreadsheet(spreadsheet_url_or_id=ref)
        return {"success": ok}
    except Exception as e:
        logger.error("Failed to delete Excel workbook: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to delete workbook: {e}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/share",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Excel Action"],
    summary="Share an Excel workbook (drive permissions)",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Share result; link shares may include webViewLink",
                example={"webViewLink": "https://contoso.sharepoint.com/..."},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether sharing succeeded",
                example=True,
            ),
        }
    ),
)
async def share_excel_workbook(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    share_type: str = "link",
    link_scope: str = "anyone",
    email: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Create sharing permission on the workbook file."""
    action = await require_typed_action(
        action_id,
        MicrosoftExcelAction,
        not_found_message=f"Microsoft Excel action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftExcelAction",
    )

    ref = _workbook_ref(action, spreadsheet_id, spreadsheet_url)

    if share_type == "user" and not email:
        raise ValidationError(
            message="Email is required for 'user' share type",
            details={"share_type": share_type},
        )

    try:
        result = await action.share_spreadsheet(
            spreadsheet_url_or_id=ref,
            share_type=share_type,
            link_scope=link_scope,
            email=email,
            role=role,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error("Failed to share Excel workbook: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to share workbook: {e}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Microsoft Excel Action"],
    summary="Create a new Excel workbook on OneDrive",
    response=success_response(
        data={
            "spreadsheet": ResponseField(
                field_type=Dict[str, Any],
                description="New workbook metadata",
                example={
                    "id": "01BYE5RZ...",
                    "name": "Book.xlsx",
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the workbook was created successfully",
                example=True,
            ),
        }
    ),
)
async def create_excel_workbook(action_id: str, title: str) -> Dict[str, Any]:
    """Create a new empty Excel workbook owned by the signed-in user."""
    action = await require_typed_action(
        action_id,
        MicrosoftExcelAction,
        not_found_message=f"Microsoft Excel action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a MicrosoftExcelAction",
    )

    try:
        created = await action.create_spreadsheet(title=title)
        return {"success": True, "spreadsheet": created}
    except Exception as e:
        logger.error("Failed to create Excel workbook: %s", e, exc_info=True)
        raise ValidationError(
            message=f"Failed to create workbook: {e}",
            details={"action_id": action_id, "title": title},
        )
