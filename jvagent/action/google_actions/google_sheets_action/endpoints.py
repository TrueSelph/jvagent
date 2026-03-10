"""API endpoints for Google Sheets action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError

from .google_sheets_action import GoogleSheetsAction

logger = logging.getLogger(__name__)

async def _get_sheets_action(action_id: str):
    """Resolve action by ID; validate it is a GoogleSheetsAction."""
    action = await GoogleSheetsAction.get(action_id)
    if action and isinstance(action, GoogleSheetsAction):
        return action
    return None

@endpoint(
    "/actions/{action_id}/google_sheets/auth_url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
)
async def get_sheets_auth_url(action_id: str) -> Dict[str, Any]:
    """Get the Google OAuth2 authorization URL."""
    action = await _get_sheets_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Sheets action {action_id} not found")

    auth_url = await action.get_authorization_url()
    return {"success": True, "auth_url": auth_url}

@endpoint(
    "/actions/{action_id}/google_sheets/authorize",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
)
async def authorize_sheets(action_id: str, code: str) -> Dict[str, Any]:
    """Exchange the authorization code for credentials."""
    action = await _get_sheets_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Sheets action {action_id} not found")

    success = await action.authorize(code)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/google_sheets/read",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
)
async def read_sheets(action_id: str, spreadsheet_id: str, range_name: str) -> Dict[str, Any]:
    """Read values from a spreadsheet."""
    action = await _get_sheets_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Sheets action {action_id} not found")

    values = await action.read_spreadsheet(spreadsheet_id=spreadsheet_id, range_name=range_name)
    return {"success": True, "values": values}

@endpoint(
    "/actions/{action_id}/google_sheets/update",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
)
async def update_sheets(
    action_id: str,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "RAW",
) -> Dict[str, Any]:
    """Update values in a spreadsheet."""
    action = await _get_sheets_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Sheets action {action_id} not found")

    result = await action.update_spreadsheet(
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        values=values,
        value_input_option=value_input_option,
    )
    return {"success": True, "result": result}

@endpoint(
    "/actions/{action_id}/google_sheets/append",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Google Sheets Action"],
)
async def append_sheets(
    action_id: str,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
    value_input_option: str = "RAW",
) -> Dict[str, Any]:
    """Append values to a spreadsheet."""
    action = await _get_sheets_action(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Google Sheets action {action_id} not found")

    result = await action.append_spreadsheet(
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        values=values,
        value_input_option=value_input_option,
    )
    return {"success": True, "result": result}
