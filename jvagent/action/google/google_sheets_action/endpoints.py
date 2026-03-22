"""API endpoints for Google Sheets action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .google_sheets_action import GoogleSheetsAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/read",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Read values from a Google Sheets spreadsheet",
    response=success_response(
        data={
            "values": ResponseField(
                field_type=List[List[Any]],
                description="2D array of cell values from the requested range",
                example=[
                    ["Name", "Score", "Grade"],
                    ["Alice", "95", "A"],
                    ["Bob", "82", "B"],
                ],
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the read was successful",
                example=True,
            ),
        }
    ),
)
async def read_sheets(
    action_id: str, spreadsheet_id: str, range_name: str
) -> Dict[str, Any]:
    """Read cell values from a Google Sheets spreadsheet.

    **Overview:**

    Retrieves a rectangular block of values from the specified range in a spreadsheet.
    Returns a 2D list where each inner list represents a row of cell values.

    **Args:**

    - action_id: ID of the Google Sheets action
    - spreadsheet_id: Unique ID of the spreadsheet (from the URL: .../spreadsheets/d/{spreadsheet_id}/...)
    - range_name: A1-notation range to read, e.g. \"Sheet1!A1:C10\" or \"A1:B5\"

    **Returns:**

    Dictionary containing:
    - **values**: 2D list of cell values. Empty cells may be omitted from trailing columns/rows
    - **success**: Always True if retrieval completes

    **Raises:**

    - ResourceNotFoundError: If the Google Sheets action is not found
    - ValidationError: If the read operation fails
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    try:
        values = await action.read_spreadsheet(
            spreadsheet_id=spreadsheet_id, range_name=range_name
        )
        return {"success": True, "values": values}
    except Exception as e:
        logger.error(f"Failed to read spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to read spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/update",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Update values in a Google Sheets spreadsheet",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Update response from the Sheets API including updated range and cell counts",
                example={
                    "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",  # pragma: allowlist secret
                    "updatedRange": "Sheet1!A1:C3",
                    "updatedRows": 3,
                    "updatedColumns": 3,
                    "updatedCells": 9,
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the update was successful",
                example=True,
            ),
        }
    ),
)
async def update_sheets(
    action_id: str,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "RAW",
) -> Dict[str, Any]:
    """Update cell values in a Google Sheets spreadsheet.

    **Overview:**

    Overwrites the specified range with the provided values. The range must match
    the dimensions of the values array.

    **Args:**

    - action_id: ID of the Google Sheets action
    - spreadsheet_id: Unique ID of the spreadsheet
    - range_name: A1-notation range to update, e.g. \"Sheet1!A1:C3\"
    - values: 2D list of values to write. Each inner list represents one row
    - value_input_option: How input data should be interpreted. \"RAW\" stores values as-is;
      \"USER_ENTERED\" parses them as if typed by a user (formulas, dates, etc.). default=\"RAW\"

    **Returns:**

    Dictionary containing:
    - **result**: Sheets API update response with updatedRange, updatedRows, updatedColumns, updatedCells
    - **success**: Always True if the update completes

    **Raises:**

    - ResourceNotFoundError: If the Google Sheets action is not found
    - ValidationError: If the update operation fails
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    try:
        result = await action.update_spreadsheet(
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to update spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to update spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/append",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Append values to a Google Sheets spreadsheet",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Append response from the Sheets API including the range where data was appended",
                example={
                    "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",  # pragma: allowlist secret
                    "tableRange": "Sheet1!A1:C10",
                    "updates": {
                        "updatedRange": "Sheet1!A11:C11",
                        "updatedRows": 1,
                        "updatedColumns": 3,
                        "updatedCells": 3,
                    },
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the append was successful",
                example=True,
            ),
        }
    ),
)
async def append_sheets(
    action_id: str,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "RAW",
) -> Dict[str, Any]:
    """Append values to a Google Sheets spreadsheet.

    **Overview:**

    Appends rows of data after the last row of existing data in the specified range.
    The API automatically detects where the current data ends and appends below it.

    **Args:**

    - action_id: ID of the Google Sheets action
    - spreadsheet_id: Unique ID of the spreadsheet
    - range_name: A1-notation range used to determine which table to append to, e.g. \"Sheet1!A1\"
    - values: 2D list of values to append. Each inner list represents one row
    - value_input_option: How input data should be interpreted. \"RAW\" stores values as-is;
      \"USER_ENTERED\" parses them as if typed by a user (formulas, dates, etc.). default=\"RAW\"

    **Returns:**

    Dictionary containing:
    - **result**: Sheets API append response with tableRange and a nested updates object
    - **success**: Always True if the append completes

    **Raises:**

    - ResourceNotFoundError: If the Google Sheets action is not found
    - ValidationError: If the append operation fails
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    try:
        result = await action.append_spreadsheet(
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to append to spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to append to spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Create a new Google Sheets spreadsheet",
    response=success_response(
        data={
            "spreadsheet": ResponseField(
                field_type=Dict[str, Any],
                description="The newly created spreadsheet metadata",
                example={
                    "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",  # pragma: allowlist secret
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the spreadsheet was created successfully",
                example=True,
            ),
        }
    ),
)
async def create_spreadsheet(action_id: str, title: str) -> Dict[str, Any]:
    """Create a new Google Sheets spreadsheet.

    **Overview:**

    Creates a blank spreadsheet with the given title in the authenticated Google account.

    **Args:**

    - action_id: ID of the Google Sheets action
    - title: Display name for the new spreadsheet

    **Returns:**

    Dictionary containing:
    - **spreadsheet**: Metadata of the created spreadsheet, including its spreadsheetId
    - **success**: Always True if creation completes

    **Raises:**

    - ResourceNotFoundError: If the Google Sheets action is not found
    - ValidationError: If the spreadsheet creation fails
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    try:
        result = await action.create_spreadsheet(title=title)
        return {"success": True, "spreadsheet": result}
    except Exception as e:
        logger.error(f"Failed to create spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to create spreadsheet: {str(e)}",
            details={"action_id": action_id, "title": title},
        )
