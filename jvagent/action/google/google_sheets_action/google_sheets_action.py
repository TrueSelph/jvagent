"""Google Sheets via Sheets API v4 and Drive v3 (share/delete file). No gspread."""

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def resolve_spreadsheet_id(spreadsheet_url_or_id: str) -> str:
    """Return spreadsheet id from a full Google Sheets URL or pass through a raw id."""
    s = spreadsheet_url_or_id.strip()
    if "docs.google.com/spreadsheets/d/" in s:
        m = _SPREADSHEET_URL_RE.search(s)
        if not m:
            raise ValueError(f"Could not parse spreadsheet id from URL: {s!r}")
        return m.group(1)
    return s


def qualify_sheet_title(title: str) -> str:
    """Quote sheet title for A1 notation when needed (spaces, specials, leading digit)."""
    if not title:
        return title
    needs_quote = (
        " " in title
        or "'" in title
        or any(not (c.isalnum() or c == "_") for c in title)
        or title[0].isdigit()
    )
    if needs_quote:
        return "'" + title.replace("'", "''") + "'"
    return title


def col_letters_to_index(letters: str) -> int:
    """Convert Excel-style column letters (A, B, …, Z, AA, …) to a 0-based index."""
    u = letters.upper().strip()
    if not u or not u.isalpha():
        raise ValueError(f"Invalid column letters: {letters!r}")
    n = 0
    for ch in u:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Invalid column letters: {letters!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def parse_a1_cell(ref: str) -> Tuple[int, int]:
    """Parse a single A1/R1C1-style cell ref into (row_index, col_index), 0-based."""
    ref = ref.replace("$", "").strip()
    if not ref:
        raise ValueError("Empty cell reference")
    i = 0
    while i < len(ref) and ref[i].isalpha():
        i += 1
    if i == 0 or i == len(ref):
        raise ValueError(f"Invalid cell reference: {ref!r}")
    col_part, row_part = ref[:i], ref[i:]
    if not row_part.isdigit():
        raise ValueError(f"Invalid cell reference: {ref!r}")
    row = int(row_part) - 1
    col = col_letters_to_index(col_part)
    return row, col


def a1_area_to_grid_range(cell_area: str) -> Tuple[int, int, int, int]:
    """Return (start_row, end_row, start_col, end_col) with end indices exclusive (API style)."""
    cell_area = cell_area.strip()
    if not cell_area:
        raise ValueError("Cell range is empty")
    if ":" in cell_area:
        a, b = cell_area.split(":", 1)
        r1, c1 = parse_a1_cell(a.strip())
        r2, c2 = parse_a1_cell(b.strip())
    else:
        r1, c1 = parse_a1_cell(cell_area)
        r2, c2 = r1, c1
    start_row = min(r1, r2)
    end_row = max(r1, r2) + 1
    start_col = min(c1, c2)
    end_col = max(c1, c2) + 1
    return start_row, end_row, start_col, end_col


def split_qualified_a1(qualified: str) -> Tuple[str, str]:
    """Split ``Sheet!A1:B2`` into (sheet_title, cell_area). Supports quoted sheet titles."""
    q = qualified.strip()
    if "!" not in q:
        raise ValueError(
            f"Range must name a worksheet (e.g. Sheet1!A1:B2); got {qualified!r}"
        )
    if q.startswith("'"):
        i = 1
        title_chars: List[str] = []
        while i < len(q):
            if q[i] == "'":
                if i + 1 < len(q) and q[i + 1] == "'":
                    title_chars.append("'")
                    i += 2
                    continue
                i += 1
                if i < len(q) and q[i] == "!":
                    sheet = "".join(title_chars)
                    rest = q[i + 1 :].strip()
                    if not rest:
                        raise ValueError(f"No A1 range after sheet in {qualified!r}")
                    return sheet, rest
                raise ValueError(f"Malformed quoted sheet in {qualified!r}")
            title_chars.append(q[i])
            i += 1
        raise ValueError(f"Unclosed sheet quote in {qualified!r}")
    bang = q.index("!")
    sheet = q[:bang].strip()
    rest = q[bang + 1 :].strip()
    if not sheet or not rest:
        raise ValueError(f"Invalid qualified range {qualified!r}")
    return sheet, rest


def compose_a1_range(
    worksheet_title: str,
    range_name: Optional[str],
) -> str:
    """
    Build an A1 range for the Sheets API.

    If range_name already includes a sheet (contains '!'), return it unchanged.
    If range_name is empty or None, return only the qualified sheet title (whole tab).
    """
    if range_name and "!" in range_name:
        return range_name
    qt = qualify_sheet_title(worksheet_title)
    if not range_name:
        return qt
    return f"{qt}!{range_name}"


class GoogleSheetsAction(GoogleAction):
    """Action for Google Sheets using OAuth2 (user-delegated credentials).

    Uses google-api-python-client (Sheets v4, Drive v3). Adding ``drive.file`` scope may
    require users to re-authorize if they previously granted only spreadsheets scope.
    """

    worksheet_title: str = attribute(
        default="Sheet1",
        description="Default worksheet (tab) title when range has no sheet name",
    )

    spreadsheet_url: str = attribute(
        default="https://docs.google.com/spreadsheets/d/1VEVd3P7AqDZZFNB5NH1BvouYDHMEuo0QDdEXp8MVpss/edit",
        description=(
            "Default spreadsheet URL or id when spreadsheet_id / spreadsheet_url are omitted"
        ),
    )

    API_SERVICE_NAME: ClassVar[str] = "sheets"
    API_VERSION: ClassVar[str] = "v4"
    # drive.file: share/delete spreadsheet files created or opened with this app.
    SCOPES: ClassVar[List[str]] = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    _built_drive_service: Optional[Any] = None

    def _effective_worksheet_title(self, worksheet_title: Optional[str]) -> str:
        return (
            worksheet_title
            if worksheet_title is not None and worksheet_title != ""
            else self.worksheet_title
        )

    def _resolve_spreadsheet_url_or_id(
        self, spreadsheet_url_or_id: Optional[str] = None
    ) -> str:
        if spreadsheet_url_or_id and str(spreadsheet_url_or_id).strip():
            return str(spreadsheet_url_or_id).strip()
        if self.spreadsheet_url and str(self.spreadsheet_url).strip():
            return str(self.spreadsheet_url).strip()
        raise ValueError(
            "Provide spreadsheet_url_or_id, or set spreadsheet_url on the GoogleSheetsAction"
        )

    def _a1_range(
        self, range_name: str, worksheet_title: Optional[str] = None
    ) -> str:
        return compose_a1_range(
            self._effective_worksheet_title(worksheet_title),
            range_name if range_name else None,
        )

    def _qualify_ranges(
        self,
        ranges: List[str],
        worksheet_title: Optional[str] = None,
    ) -> List[str]:
        ws = self._effective_worksheet_title(worksheet_title)
        out: List[str] = []
        for r in ranges:
            r = r.strip()
            if not r:
                continue
            out.append(compose_a1_range(ws, r))
        return out

    async def _get_drive_service(self):
        if self._built_drive_service and getattr(
            self._built_drive_service._http, "credentials", None
        ):
            if self._built_drive_service._http.credentials.valid:
                return self._built_drive_service
            logger.info("Cached Drive service credentials invalid; rebuilding.")

        creds = await self._get_credentials()
        self._built_drive_service = build(
            "drive", "v3", credentials=creds, static_discovery=False
        )
        return self._built_drive_service

    async def _get_sheet_id_by_title(self, spreadsheet_id: str, title: str) -> int:
        service = await self.get_service()
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))")
            .execute()
        )
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == title:
                return int(props["sheetId"])
        raise ValueError(f"No worksheet titled {title!r} in this spreadsheet")

    async def read_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        worksheet_title: Optional[str] = None,
    ) -> List[List[Any]]:
        """Read values. Pass range_name like A1:C10 or leave empty for the whole tab."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        full_range = self._a1_range(range_name, worksheet_title)
        service = await self.get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=full_range)
            .execute()
        )
        return result.get("values", [])

    async def update_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update values in a range (sheet-local A1 allowed when worksheet_title is set)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        if values is None:
            raise ValueError("values is required")
        if not range_name.strip():
            raise ValueError("range_name is required for update")
        full_range = self._a1_range(range_name, worksheet_title)
        service = await self.get_service()
        body = {"values": values}
        return (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=full_range,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    async def append_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append rows; range is usually a single cell or row anchor (e.g. A1)."""
        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        if values is None:
            raise ValueError("values is required")
        if not range_name.strip():
            raise ValueError("range_name is required for append")
        full_range = self._a1_range(range_name, worksheet_title)
        service = await self.get_service()
        body = {"values": values}
        return (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=full_range,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute()
        )

    async def batch_clear(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        ranges: Optional[List[str]] = None,
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Clear one or more ranges; list items may be sheet-local A1."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        if ranges is None:
            raise ValueError("ranges is required")
        qualified = self._qualify_ranges(ranges, worksheet_title)
        if not qualified:
            raise ValueError("At least one non-empty range is required")
        service = await self.get_service()
        return (
            service.spreadsheets()
            .values()
            .batchClear(spreadsheetId=spreadsheet_id, body={"ranges": qualified})
            .execute()
        )

    async def _grid_range_dict(
        self,
        spreadsheet_id: str,
        range_name: str,
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not str(range_name).strip():
            raise ValueError("range_name is required")
        full = self._a1_range(range_name.strip(), worksheet_title)
        sheet_title, cell_area = split_qualified_a1(full)
        sheet_id = await self._get_sheet_id_by_title(spreadsheet_id, sheet_title)
        sr, er, sc, ec = a1_area_to_grid_range(cell_area)
        return {
            "sheetId": sheet_id,
            "startRowIndex": sr,
            "endRowIndex": er,
            "startColumnIndex": sc,
            "endColumnIndex": ec,
        }

    async def format_cells(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        worksheet_title: Optional[str] = None,
        user_entered_format: Optional[Dict[str, Any]] = None,
        fields: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply ``userEnteredFormat`` to a range via ``repeatCell`` (batchUpdate)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url
        if not worksheet_title:
            worksheet_title = self.worksheet_title
        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        if not user_entered_format:
            raise ValueError("user_entered_format is required")
        grid_range = await self._grid_range_dict(
            spreadsheet_id, range_name, worksheet_title
        )
        field_mask = fields if fields is not None else "userEnteredFormat"
        service = await self.get_service()
        body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": grid_range,
                        "cell": {"userEnteredFormat": user_entered_format},
                        "fields": field_mask,
                    }
                }
            ]
        }
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    _MERGE_TYPES = frozenset({"MERGE_ALL", "MERGE_COLUMNS", "MERGE_ROWS"})

    async def merge_cells(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        worksheet_title: Optional[str] = None,
        merge_type: str = "MERGE_ALL",
    ) -> Dict[str, Any]:
        """Merge a rectangular range (``mergeCells`` batchUpdate)."""

        if merge_type not in self._MERGE_TYPES:
            raise ValueError(
                f"merge_type must be one of {sorted(self._MERGE_TYPES)}; got {merge_type!r}"
            )
        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url
        if not worksheet_title:
            worksheet_title = self.worksheet_title
        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        grid_range = await self._grid_range_dict(
            spreadsheet_id, range_name, worksheet_title
        )
        service = await self.get_service()
        body = {
            "requests": [
                {"mergeCells": {"range": grid_range, "mergeType": merge_type}}
            ]
        }
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    async def unmerge_cells(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Split merged cells in a rectangular range (``unmergeCells`` batchUpdate)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url
        if not worksheet_title:
            worksheet_title = self.worksheet_title
        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        grid_range = await self._grid_range_dict(
            spreadsheet_id, range_name, worksheet_title
        )
        service = await self.get_service()
        body = {"requests": [{"unmergeCells": {"range": grid_range}}]}
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    async def create_spreadsheet(self, title: str) -> Dict[str, Any]:
        """Create a spreadsheet; response includes id, url, and title."""
        service = await self.get_service()
        spreadsheet = {"properties": {"title": title}}
        return (
            service.spreadsheets()
            .create(
                body=spreadsheet,
                fields="spreadsheetId,spreadsheetUrl,properties(title)",
            )
            .execute()
        )

    async def create_worksheet(
        self,
        title: str,
        spreadsheet_url_or_id: Optional[str] = None,
        rows: int = 1000,
        cols: int = 26,
    ) -> Dict[str, Any]:
        """Add a new tab (worksheet) to an existing spreadsheet."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        service = await self.get_service()
        body = {
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": title,
                            "gridProperties": {
                                "rowCount": rows,
                                "columnCount": cols,
                            },
                        }
                    }
                }
            ]
        }
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    async def update_worksheet(
        self,
        worksheet_title: str,
        spreadsheet_url_or_id: Optional[str] = None,
        new_title: Optional[str] = None,
        rows: Optional[int] = None,
        cols: Optional[int] = None,
        hidden: Optional[bool] = None,
        tab_color: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Rename a tab and/or resize grid, visibility, or tab color (Sheets batchUpdate)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        if not str(worksheet_title).strip():
            raise ValueError("worksheet_title is required")
        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        sheet_id = await self._get_sheet_id_by_title(spreadsheet_id, worksheet_title)

        props: Dict[str, Any] = {"sheetId": sheet_id}
        field_masks: List[str] = []

        if new_title is not None:
            props["title"] = new_title
            field_masks.append("title")
        if rows is not None or cols is not None:
            grid_props: Dict[str, int] = {}
            if rows is not None:
                grid_props["rowCount"] = rows
                field_masks.append("gridProperties.rowCount")
            if cols is not None:
                grid_props["columnCount"] = cols
                field_masks.append("gridProperties.columnCount")
            props["gridProperties"] = grid_props
        if hidden is not None:
            props["hidden"] = hidden
            field_masks.append("hidden")
        if tab_color is not None:
            props["tabColor"] = tab_color
            field_masks.append("tabColor")

        if not field_masks:
            raise ValueError(
                "Provide at least one of: new_title, rows, cols, hidden, tab_color"
            )

        service = await self.get_service()
        body = {
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": props,
                        "fields": ",".join(field_masks),
                    }
                }
            ]
        }
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    async def delete_worksheet(
        self,
        worksheet_title: str,
        spreadsheet_url_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove a tab by title."""

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        sheet_id = await self._get_sheet_id_by_title(spreadsheet_id, worksheet_title)
        service = await self.get_service()
        body = {"requests": [{"deleteSheet": {"sheetId": sheet_id}}]}
        return (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )

    async def share_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        share_type: str = "link",
        link_scope: str = "anyone",
        email: Optional[str] = None,
        role: str = "reader",
    ) -> Dict[str, Any]:
        """Share via Drive permissions (same semantics as GoogleDriveAction.share_file)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        file_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        service = await self._get_drive_service()

        if share_type == "link":
            permission = {"type": link_scope, "role": role}
        else:
            permission = {"type": "user", "role": role, "emailAddress": email}

        service.permissions().create(fileId=file_id, body=permission).execute()

        if share_type == "link":
            file = service.files().get(fileId=file_id, fields="webViewLink").execute()
            return {"webViewLink": file.get("webViewLink")}

        return {"success": True}

    async def delete_spreadsheet(
        self, spreadsheet_url_or_id: Optional[str] = None
    ) -> bool:
        """Permanently delete the spreadsheet file (Drive API)."""

        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        file_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        service = await self._get_drive_service()
        service.files().delete(fileId=file_id).execute()
        return True
