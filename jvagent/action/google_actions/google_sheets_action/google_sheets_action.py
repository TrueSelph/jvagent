import logging
from typing import Any, ClassVar, Dict, List

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleSheetsAction(GoogleAction):
    """Action for Google Sheets operations using a service account."""

    API_SERVICE_NAME: ClassVar[str] = "sheets"
    API_VERSION: ClassVar[str] = "v4"
    SCOPES: ClassVar[List[str]] = ["https://www.googleapis.com/auth/spreadsheets"]

    async def read_spreadsheet(
        self, spreadsheet_id: str, range_name: str
    ) -> List[List[Any]]:
        """Read values from a spreadsheet."""
        service = await self.get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return result.get("values", [])

    async def update_spreadsheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[Any]],
        value_input_option: str = "RAW",
    ) -> Dict[str, Any]:
        """Update values in a spreadsheet."""
        service = await self.get_service()
        body = {"values": values}
        return (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    async def append_spreadsheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[Any]],
        value_input_option: str = "RAW",
    ) -> Dict[str, Any]:
        """Append values to a spreadsheet."""
        service = await self.get_service()
        body = {"values": values}
        return (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    async def create_spreadsheet(self, title: str) -> Dict[str, Any]:
        """Create a new spreadsheet."""
        service = await self.get_service()
        spreadsheet = {"properties": {"title": title}}
        return (
            service.spreadsheets()
            .create(body=spreadsheet, fields="spreadsheetId")
            .execute()
        )
