"""API endpoints for Google Sheets action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError

from jvagent.action.utils.endpoint_helpers import require_typed_action

from .google_sheets_action import GoogleSheetsAction

logger = logging.getLogger(__name__)


def _spreadsheet_ref(
    action: GoogleSheetsAction,
    spreadsheet_id: Optional[str],
    spreadsheet_url: Optional[str],
) -> str:
    """Pick which spreadsheet this request targets.

    Resolution order:

    1. Non-empty ``spreadsheet_url`` (full Sheets URL or raw id).
    2. Non-empty ``spreadsheet_id``.
    3. The action's configured :attr:`~GoogleSheetsAction.spreadsheet_url` default.

    Args:
        action: Resolves the fallback spreadsheet when query/body params omit one.
        spreadsheet_id: Spreadsheet id from the request.
        spreadsheet_url: Spreadsheet URL or id from the request.

    Returns:
        Stripped URL or id string for ``read_spreadsheet`` / ``update_spreadsheet`` / etc.

    Raises:
        ValidationError: When no non-empty reference is available from any source.
    """
    if spreadsheet_url and str(spreadsheet_url).strip():
        return str(spreadsheet_url).strip()
    if spreadsheet_id and str(spreadsheet_id).strip():
        return str(spreadsheet_id).strip()
    if action.spreadsheet_url and str(action.spreadsheet_url).strip():
        return str(action.spreadsheet_url).strip()
    raise ValidationError(
        message=(
            "Provide spreadsheet_url or spreadsheet_id, or set spreadsheet_url on the action"
        ),
        details={"spreadsheet_id": spreadsheet_id, "spreadsheet_url": spreadsheet_url},
    )


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
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch cell values from a spreadsheet via the Sheets API ``values.get`` call.

    The spreadsheet to read is chosen by :func:`_spreadsheet_ref` (explicit id/url in the
    request, else the action default). Ranges use A1 notation: either a **sheet-qualified**
    range in ``range_name`` (e.g. ``Sheet1!A1:C10`` or ``'My Tab'!A1``), or a **local**
    fragment (e.g. ``A1:C10``) combined with ``worksheet_title`` (defaults to the action's
    :attr:`~GoogleSheetsAction.worksheet_title`, usually ``Sheet1``). An empty
    ``range_name`` reads the **entire** worksheet named by ``worksheet_title``.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id; used if ``spreadsheet_url`` is absent.
        spreadsheet_url: Optional full Sheets URL or id; takes precedence over ``spreadsheet_id``.
        range_name: A1 range within the tab, full ``Sheet!range``, or ``""`` for whole tab.
        worksheet_title: Tab title when ``range_name`` has no ``!``; defaults via the action.

    Returns:
        ``{"success": True, "values": ...}`` where ``values`` is a list of rows (each row a
        list of cell values). Empty cells may be omitted from trailing positions per API behavior.

    Raises:
        ValidationError: Missing spreadsheet reference, action not found / wrong type, or
            Sheets API failure (message includes the underlying error).
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

    try:
        values = await action.read_spreadsheet(
            spreadsheet_url_or_id=ref,
            range_name=range_name or "",
            worksheet_title=worksheet_title,
        )
        return {"success": True, "values": values}
    except Exception as e:
        logger.error(f"Failed to read spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to read spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_url": spreadsheet_url,
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
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    values: Optional[List[List[Any]]] = None,
    value_input_option: str = "RAW",
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Overwrite a rectangular range with ``values`` (Sheets API ``values.update``).

    ``range_name`` is **required** and must be non-empty after strip: sheet-local A1 (e.g.
    ``A1:C3``) with ``worksheet_title``, or a single string already containing ``!`` for the
    tab name. The payload ``values`` is a 2D list aligned with that range; fewer rows/columns
    than the range may partially fill it depending on API rules.

    ``value_input_option`` is passed through to Google: ``RAW`` stores values as-is;
    ``USER_ENTERED`` parses strings as numbers, dates, formulas, etc. (see Sheets API docs).

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id; overrides ``spreadsheet_id`` when set.
        range_name: Target A1 range (required).
        values: 2D array of new cell values (required).
        value_input_option: ``RAW`` (default) or ``USER_ENTERED``, etc.
        worksheet_title: Tab when ``range_name`` is not sheet-qualified.

    Returns:
        ``{"success": True, "result": ...}`` with the API response (updated range, cell counts).

    Raises:
        ValidationError: If ``values`` is omitted, ``range_name`` is invalid/empty, spreadsheet
            reference is missing, or the API returns an error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if values is None:
        raise ValidationError(
            message="values is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.update_spreadsheet(
            spreadsheet_url_or_id=ref,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
            worksheet_title=worksheet_title,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to update spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to update spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_url": spreadsheet_url,
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
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    values: Optional[List[List[Any]]] = None,
    value_input_option: str = "RAW",
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Append ``values`` as new rows after existing data (Sheets API ``values.append``).

    ``range_name`` is **optional**. When omitted or blank, the range is the **entire worksheet**
    named by ``worksheet_title`` (defaulting to the action's tab), and Google picks the table
    extent and insertion point. When set, ``range_name`` is the **table anchor**—often a single
    cell or header row (e.g. ``A1`` or ``A1:Z1``). Combine with ``worksheet_title`` when
    ``range_name`` does not include a sheet name.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        range_name: Append anchor A1 range, or empty for whole-tab append.
        values: Rows to append as a 2D list (required).
        value_input_option: ``RAW`` (default) or ``USER_ENTERED``.
        worksheet_title: Tab when ``range_name`` is not sheet-qualified.

    Returns:
        ``{"success": True, "result": ...}`` including ``updates`` / ``tableRange`` from the API.

    Raises:
        ValidationError: Missing ``values``, missing spreadsheet ref, or API error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if values is None:
        raise ValidationError(
            message="values is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.append_spreadsheet(
            spreadsheet_url_or_id=ref,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
            worksheet_title=worksheet_title,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to append to spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to append to spreadsheet: {str(e)}",
            details={
                "action_id": action_id,
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_url": spreadsheet_url,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/clear",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Clear ranges in a Google Sheets spreadsheet",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchClear response from the Sheets API",
                example={
                    "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",  # pragma: allowlist secret
                    "clearedRanges": ["Sheet1!A1:B2"],
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the clear was successful",
                example=True,
            ),
        }
    ),
)
async def clear_sheets_ranges(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    ranges: Optional[List[str]] = None,
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Clear formatting and values for one or more ranges (Sheets API ``values.batchClear``).

    ``ranges`` must contain at least one non-empty string after trimming. Each entry is A1
    notation; if it does not contain ``!``, it is applied to ``worksheet_title`` (defaulting
    like other endpoints). Entries that are blank after strip are skipped; if none remain, the
    action layer raises.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        ranges: List of A1 ranges to clear (required, non-empty effective list).
        worksheet_title: Tab name for unqualified range strings.

    Returns:
        ``{"success": True, "result": ...}`` including ``clearedRanges`` from the API.

    Raises:
        ValidationError: If ``ranges`` is ``None`` or clearing fails.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if ranges is None:
        raise ValidationError(
            message="ranges is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.batch_clear(
            spreadsheet_url_or_id=ref,
            ranges=ranges,
            worksheet_title=worksheet_title,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to clear spreadsheet ranges: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to clear spreadsheet ranges: {str(e)}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/format",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Apply cell formatting to a range (repeatCell / batchUpdate)",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response from the Sheets API",
                example={},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether formatting was applied",
                example=True,
            ),
        }
    ),
)
async def format_sheets_cells(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    worksheet_title: Optional[str] = None,
    user_entered_format: Optional[Dict[str, Any]] = None,
    fields: Optional[str] = None,
) -> Dict[str, Any]:
    """Set display format on a rectangular range (fonts, colors, alignment, borders, etc.).

    Sends a ``repeatCell`` request. ``user_entered_format`` is the API ``userEnteredFormat``
    object (for example ``backgroundColor``, ``textFormat``, ``horizontalAlignment``). See
    Google Sheets API *CellData.userEnteredFormat*. ``fields`` is the FieldMask passed to
    ``repeatCell``; default ``userEnteredFormat`` applies the whole subtree you supply.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        range_name: Sheet-local or qualified A1 range (e.g. ``A1:D1``, ``Sheet2!B2:C10``).
        worksheet_title: Tab when ``range_name`` has no sheet qualifier.
        user_entered_format: Format object (required).
        fields: Optional repeatCell field mask (defaults to ``userEnteredFormat``).

    Returns:
        ``{"success": True, "result": ...}`` batchUpdate response.

    Raises:
        ValidationError: Missing format, invalid range, or API error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if user_entered_format is None:
        raise ValidationError(
            message="user_entered_format is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.format_cells(
            spreadsheet_url_or_id=ref,
            range_name=range_name,
            worksheet_title=worksheet_title,
            user_entered_format=user_entered_format,
            fields=fields,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to format spreadsheet cells: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to format spreadsheet cells: {str(e)}",
            details={
                "action_id": action_id,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/merge",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Merge a rectangular range of cells",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response from the Sheets API",
                example={},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether cells were merged",
                example=True,
            ),
        }
    ),
)
async def merge_sheets_cells(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    worksheet_title: Optional[str] = None,
    merge_type: str = "MERGE_ALL",
) -> Dict[str, Any]:
    """Merge cells in an A1 rectangle (``mergeCells`` batchUpdate).

    ``merge_type`` is one of ``MERGE_ALL`` (default), ``MERGE_ROWS``, or ``MERGE_COLUMNS``
    (Google Sheets API merge types).

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        range_name: Rectangular range (e.g. ``A1:C1``).
        worksheet_title: Tab when ``range_name`` is not sheet-qualified.
        merge_type: ``MERGE_ALL``, ``MERGE_ROWS``, or ``MERGE_COLUMNS``.

    Returns:
        ``{"success": True, "result": ...}`` batchUpdate response.

    Raises:
        ValidationError: Invalid range or API error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

    try:
        result = await action.merge_cells(
            spreadsheet_url_or_id=ref,
            range_name=range_name,
            worksheet_title=worksheet_title,
            merge_type=merge_type,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to merge spreadsheet cells: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to merge spreadsheet cells: {str(e)}",
            details={
                "action_id": action_id,
                "range_name": range_name,
                "merge_type": merge_type,
            },
        )


@endpoint(
    "/actions/{action_id}/unmerge",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Unmerge cells in a rectangular range",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response from the Sheets API",
                example={},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether unmerge completed",
                example=True,
            ),
        }
    ),
)
async def unmerge_sheets_cells(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    range_name: str = "",
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Restore individual cells in a range after a merge (``unmergeCells`` batchUpdate)."""

    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

    try:
        result = await action.unmerge_cells(
            spreadsheet_url_or_id=ref,
            range_name=range_name,
            worksheet_title=worksheet_title,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to unmerge spreadsheet cells: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to unmerge spreadsheet cells: {str(e)}",
            details={
                "action_id": action_id,
                "range_name": range_name,
            },
        )


@endpoint(
    "/actions/{action_id}/worksheet/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Create a new worksheet (tab) in a spreadsheet",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response including replies with new sheet metadata",
                example={"replies": [{"addSheet": {"properties": {"sheetId": 123}}}]},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the worksheet was created",
                example=True,
            ),
        }
    ),
)
async def create_worksheet_endpoint(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    title: str = "",
    rows: int = 1000,
    cols: int = 26,
) -> Dict[str, Any]:
    """Create a new worksheet (tab) on an existing spreadsheet (``batchUpdate`` + ``addSheet``).

    ``title`` must be non-empty and becomes the tab label. ``rows`` and ``cols`` set initial
    grid dimensions (defaults 1000×26).

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        title: New tab name (required).
        rows: Initial row count for the grid.
        cols: Initial column count for the grid.

    Returns:
        ``{"success": True, "result": ...}`` with batchUpdate replies (e.g. new ``sheetId``).

    Raises:
        ValidationError: Missing ``title``, missing spreadsheet ref, duplicate tab title, or API error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if not str(title).strip():
        raise ValidationError(
            message="title is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.create_worksheet(
            title=title,
            spreadsheet_url_or_id=ref,
            rows=rows,
            cols=cols,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to create worksheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to create worksheet: {str(e)}",
            details={"action_id": action_id, "title": title},
        )


@endpoint(
    "/actions/{action_id}/worksheet/update",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Update worksheet (tab) properties: rename, grid size, hidden, tab color",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response",
                example={},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the worksheet was updated",
                example=True,
            ),
        }
    ),
)
async def update_worksheet_endpoint(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    worksheet_title: Optional[str] = None,
    new_title: Optional[str] = None,
    rows: Optional[int] = None,
    cols: Optional[int] = None,
    hidden: Optional[bool] = None,
    tab_color: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Change properties of the worksheet named ``worksheet_title`` (Sheets ``batchUpdate``).

    ``worksheet_title`` selects the **existing** tab (defaults to the action's
    :attr:`~GoogleSheetsAction.worksheet_title` when omitted). The title must be non-blank
    after resolution and must match a sheet in the spreadsheet. Provide **at least one** of
    ``new_title``, ``rows``, ``cols``, ``hidden``, or ``tab_color``. Omitted axes are left
    unchanged on partial grid
    updates. ``tab_color`` should follow the API shape, e.g. ``{"red": 1, "green": 0, "blue": 0}``
    with components in the 0–1 range.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        worksheet_title: Current title of the tab to modify (must exist on the spreadsheet).
        new_title: Rename the tab to this string when set.
        rows: New row count for the tab grid when set.
        cols: New column count when set.
        hidden: Whether the tab is hidden when set.
        tab_color: ``tabColor`` object for the tab when set.

    Returns:
        ``{"success": True, "result": ...}`` batchUpdate response.

    Raises:
        ValidationError: If no property fields are provided, the tab title is missing/unknown,
            or the API fails.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

    if all(x is None for x in (new_title, rows, cols, hidden, tab_color)):
        raise ValidationError(
            message="Provide at least one of: new_title, rows, cols, hidden, tab_color",
            details={"action_id": action_id},
        )

    try:
        result = await action.update_worksheet(
            worksheet_title=worksheet_title,
            spreadsheet_url_or_id=ref,
            new_title=new_title,
            rows=rows,
            cols=cols,
            hidden=hidden,
            tab_color=tab_color,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to update worksheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to update worksheet: {str(e)}",
            details={"action_id": action_id, "worksheet_title": worksheet_title},
        )


@endpoint(
    "/actions/{action_id}/worksheet/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Delete a worksheet (tab) from a spreadsheet",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="batchUpdate response",
                example={},
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether the worksheet was deleted",
                example=True,
            ),
        }
    ),
)
async def delete_worksheet_endpoint(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    worksheet_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a worksheet (tab) by exact title (``batchUpdate`` + ``deleteSheet``).

    ``worksheet_title`` is required and must match an existing tab. This cannot be undone via
    this API (restore from version history in the Sheets UI if needed).

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        worksheet_title: Title of the tab to remove (required).

    Returns:
        ``{"success": True, "result": ...}`` batchUpdate response.

    Raises:
        ValidationError: If ``worksheet_title`` is missing/blank, the tab does not exist, or
            the API returns an error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)
    if not str(worksheet_title).strip():
        raise ValidationError(
            message="worksheet_title is required",
            details={"action_id": action_id},
        )

    try:
        result = await action.delete_worksheet(
            worksheet_title=worksheet_title,
            spreadsheet_url_or_id=ref,
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to delete worksheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to delete worksheet: {str(e)}",
            details={
                "action_id": action_id,
                "worksheet_title": worksheet_title,
            },
        )


@endpoint(
    "/actions/{action_id}/share",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Share a Google Spreadsheet (Drive permissions)",
    response=success_response(
        data={
            "result": ResponseField(
                field_type=Dict[str, Any],
                description="Share result; link shares may include webViewLink",
                example={
                    "webViewLink": "https://docs.google.com/spreadsheets/d/.../edit?usp=sharing"
                },
            ),
            "success": ResponseField(
                field_type=bool,
                description="Whether sharing succeeded",
                example=True,
            ),
        }
    ),
)
async def share_spreadsheet_endpoint(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
    share_type: str = "link",
    link_scope: str = "anyone",
    email: Optional[str] = None,
    role: str = "reader",
) -> Dict[str, Any]:
    """Create a Drive permission on the spreadsheet (share link or specific user).

    Uses the Drive API ``permissions.create`` on the resolved file id. For ``share_type ==
    "link"``, a permission with ``type`` = ``link_scope`` (default ``anyone``) and
    ``role`` (default ``reader``) is applied; the response may include ``webViewLink``. For
    user invites, set ``share_type`` to ``"user"`` and supply ``email``; validation requires
    ``email`` in that mode.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.
        share_type: ``"link"`` for link-based access (default) or ``"user"`` for email-based.
        link_scope: Drive permission ``type`` for link shares (e.g. ``anyone``).
        email: Recipient email when ``share_type`` is ``"user"``.
        role: Drive role such as ``reader``, ``writer``, ``commenter`` (passed to the API).

    Returns:
        ``{"success": True, "result": ...}`` — often ``{"webViewLink": ...}`` for link shares
        or ``{"success": True}`` for user shares.

    Raises:
        ValidationError: Missing email for user share, missing spreadsheet ref, or Drive API error.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

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
        logger.error(f"Failed to share spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to share spreadsheet: {str(e)}",
            details={"action_id": action_id},
        )


@endpoint(
    "/actions/{action_id}/delete",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
    summary="Permanently delete a Google Spreadsheet file",
    response=success_response(
        data={
            "success": ResponseField(
                field_type=bool,
                description="Whether the spreadsheet was deleted",
                example=True,
            ),
        }
    ),
)
async def delete_spreadsheet_endpoint(
    action_id: str,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Permanently delete the spreadsheet file (Drive API ``files.delete``).

    This removes the file from the user's Drive; recovery may only be possible via Drive's
    trash/version policies. Requires the action's OAuth scopes to include Drive file access.

    Args:
        action_id: Google Sheets action resource id (path).
        spreadsheet_id: Optional spreadsheet id.
        spreadsheet_url: Optional full Sheets URL or id.

    Returns:
        ``{"success": True}`` when the action layer reports success.

    Raises:
        ValidationError: Missing spreadsheet reference or API failure.
    """
    action = await require_typed_action(
        action_id,
        GoogleSheetsAction,
        not_found_message=f"Google Sheets action {action_id} not found",
        wrong_type_message=f"Action '{action_id}' is not a GoogleSheetsAction",
    )

    ref = _spreadsheet_ref(action, spreadsheet_id, spreadsheet_url)

    try:
        ok = await action.delete_spreadsheet(spreadsheet_url_or_id=ref)
        return {"success": ok}
    except Exception as e:
        logger.error(f"Failed to delete spreadsheet: {e}", exc_info=True)
        raise ValidationError(
            message=f"Failed to delete spreadsheet: {str(e)}",
            details={"action_id": action_id},
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
                description="New spreadsheet metadata (spreadsheetId, spreadsheetUrl, properties)",
                example={
                    "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",  # pragma: allowlist secret
                    "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/.../edit",
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
    """Create a new empty spreadsheet owned by the credentials on the action (Sheets ``create``).

    Only ``title`` is set; additional tabs or data require follow-up update/append calls. The
    response is constrained to ``spreadsheetId``, ``spreadsheetUrl``, and ``properties.title``
    fields from the API.

    Args:
        action_id: Google Sheets action resource id (path).
        title: Document title for the new spreadsheet.

    Returns:
        ``{"success": True, "spreadsheet": {...}}`` with id/url metadata.

    Raises:
        ValidationError: If creation fails (e.g. quota or auth).
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
