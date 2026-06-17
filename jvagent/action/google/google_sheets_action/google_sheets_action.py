"""Google Sheets via Sheets API v4 and Drive v3 (share/delete file). No gspread.

Helpers and :class:`GoogleSheetsAction` use A1 notation. Spreadsheet targets are resolved
from a full ``docs.google.com`` URL or a raw spreadsheet id via :func:`resolve_spreadsheet_id`.
"""

import json
import logging
import re
from typing import Annotated, Any, ClassVar, Dict, List, Optional, Tuple

from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute

from jvagent.tooling.tool_decorator import tool

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def resolve_spreadsheet_id(spreadsheet_url_or_id: str) -> str:
    """Extract the spreadsheet id from input, accepting either a URL or a raw id string.

    Args:
        spreadsheet_url_or_id: Full Google Sheets URL (``.../spreadsheets/d/<id>/...``)
            or a spreadsheet id alone.

    Returns:
        The spreadsheet id substring.

    Raises:
        ValueError: If the string looks like a Sheets URL but the id cannot be parsed.
    """
    s = spreadsheet_url_or_id.strip()
    if "docs.google.com/spreadsheets/d/" in s:
        m = _SPREADSHEET_URL_RE.search(s)
        if not m:
            raise ValueError(f"Could not parse spreadsheet id from URL: {s!r}")
        return m.group(1)
    return s


def qualify_sheet_title(title: str) -> str:
    """Return an A1-safe sheet title token (quote when required by Sheets rules).

    Args:
        title: Worksheet (tab) title.

    Returns:
        Either the title unchanged or wrapped in single quotes with embedded ``'``
        doubled (e.g. ``O'Brien`` → ``'O''Brien'``).

    Raises:
        None from this helper; an empty title is returned unchanged.
    """
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
    """Map Excel-style column letters to a 0-based column index.

    Args:
        letters: Such as ``A``, ``Z``, ``AA`` (case-insensitive).

    Returns:
        Zero-based column index.

    Raises:
        ValueError: If ``letters`` is empty, non-alphabetic, or invalid.
    """
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
    """Parse one A1 cell reference into zero-based row and column indices.

    Args:
        ref: Cell reference such as ``B2`` or ``$A$1`` (``$`` is ignored).

    Returns:
        ``(row_index, col_index)`` both 0-based.

    Raises:
        ValueError: Empty ref, missing row digits, or invalid letters/digits.
    """
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
    """Convert an A1 rectangle (no sheet prefix) to grid indices for the Sheets API.

    Args:
        cell_area: A single cell (``A1``) or range (``A1:C3``).

    Returns:
        ``(start_row, end_row, start_col, end_col)`` with **end** indices exclusive,
        matching ``GridRange`` in the API.

    Raises:
        ValueError: If ``cell_area`` is empty or malformed.
    """
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
    """Split a qualified A1 range into worksheet title and cell/range part.

    Args:
        qualified: Such as ``Sheet1!A1:B2`` or ``'My Tab'!A1``.

    Returns:
        ``(sheet_title, cell_area)`` where ``cell_area`` is the fragment after ``!``.

    Raises:
        ValueError: No ``!``, malformed quotes, or missing A1 part after ``!``.
    """
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
    """Build a single range string for ``values.get`` / ``values.update`` / ``values.append``.

    If ``range_name`` already contains ``!``, it is treated as fully qualified and returned
    unchanged. If ``range_name`` is empty or ``None``, only the qualified worksheet title
    is returned (entire tab). Otherwise the result is ``<qualified_title>!<range_name>``.

    Args:
        worksheet_title: Tab name used when ``range_name`` is not qualified.
        range_name: Local A1 fragment, full ``Sheet!A1`` string, or empty/``None`` for whole tab.

    Returns:
        A range string accepted by the Sheets API.

    Raises:
        None; an empty ``worksheet_title`` with a local ``range_name`` still produces
        a valid ``!`` suffix (callers should ensure the tab exists).
    """
    if range_name and "!" in range_name:
        return range_name
    qt = qualify_sheet_title(worksheet_title)
    if not range_name:
        return qt
    return f"{qt}!{range_name}"


class GoogleSheetsAction(GoogleAction):
    """Google Sheets operations with OAuth2 (user-delegated credentials).

    Uses google-api-python-client (Sheets v4, Drive v3). Adding ``drive.file`` scope may
    require users to re-authorize if they previously granted only spreadsheets scope.

    Instance attributes :attr:`worksheet_title` and :attr:`spreadsheet_url` supply defaults
    when methods omit ``worksheet_title`` or ``spreadsheet_url_or_id`` (same resolution idea
    as HTTP handlers that fall back to the action configuration).
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
        """Return explicit ``worksheet_title`` if set, else the action default tab name."""
        return (
            worksheet_title
            if worksheet_title is not None and worksheet_title != ""
            else self.worksheet_title
        )

    def _resolve_spreadsheet_url_or_id(
        self, spreadsheet_url_or_id: Optional[str] = None
    ) -> str:
        """Resolve non-empty spreadsheet URL or id from arguments or ``spreadsheet_url``."""
        if spreadsheet_url_or_id and str(spreadsheet_url_or_id).strip():
            return str(spreadsheet_url_or_id).strip()
        if self.spreadsheet_url and str(self.spreadsheet_url).strip():
            return str(self.spreadsheet_url).strip()
        raise ValueError(
            "Provide spreadsheet_url_or_id, or set spreadsheet_url on the GoogleSheetsAction"
        )

    def _a1_range(self, range_name: str, worksheet_title: Optional[str] = None) -> str:
        """Combine default/explicit tab with a local A1 fragment via :func:`compose_a1_range`."""
        return compose_a1_range(
            self._effective_worksheet_title(worksheet_title),
            range_name if range_name else None,
        )

    def _qualify_ranges(
        self,
        ranges: List[str],
        worksheet_title: Optional[str] = None,
    ) -> List[str]:
        """Qualify each non-blank range string with ``worksheet_title`` when needed."""
        ws = self._effective_worksheet_title(worksheet_title)
        out: List[str] = []
        for r in ranges:
            r = r.strip()
            if not r:
                continue
            out.append(compose_a1_range(ws, r))
        return out

    async def _get_sheets_service(self):
        """Lazy Drive API v3 client (permissions, delete file); rebuilds if credentials expire."""
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
        """Return the numeric ``sheetId`` for a tab title (for ``batchUpdate`` grid ranges).

        Raises:
            ValueError: No worksheet with ``title`` exists on the spreadsheet.
        """
        service = await self.get_service()
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))"
            )
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
        """Read cell values (Sheets API ``spreadsheets.values.get``).

        The spreadsheet is taken from ``spreadsheet_url_or_id`` if non-empty, otherwise from
        :attr:`spreadsheet_url`. Use **sheet-qualified** ``range_name`` (e.g. ``Sheet1!A1:C10``)
        or a **local** fragment (e.g. ``A1:C10``) together with ``worksheet_title`` (defaulting
        to :attr:`worksheet_title`). An empty ``range_name`` reads the **entire** tab named by
        ``worksheet_title``.

        Args:
            spreadsheet_url_or_id: Full URL, id, or ``None`` to use the action default.
            range_name: A1 range, qualified range, or ``""`` for the whole worksheet.
            worksheet_title: Tab when ``range_name`` has no ``!``.

        Returns:
            ``values`` from the API: list of rows, each row a list of cell values. Trailing
            empty cells may be omitted per API behavior.

        Raises:
            ValueError: Missing spreadsheet configuration or invalid id resolution.
        """
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

    async def last_filled_row_1based(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        column: str = "A",
        worksheet_title: Optional[str] = None,
    ) -> int:
        """Best-effort last 1-based row that has a non-empty value in the given column.

        Performs a ``values.get`` on ``<column>:<column>`` (e.g. ``A:A``) on the target tab,
        then scans from the bottom of the returned rows. This is useful before a targeted
        ``update``; for appending, prefer :meth:`append_spreadsheet` with an empty range or
        inspect the append response ``updates.updatedRange``.

        **Caveats:** Large columns transfer more data. Sparse tables (gaps below the last value)
        are handled only insofar as the API returns rows—trailing blank rows are omitted by the
        API, so the last populated row in the response matches the last row with data in that
        column for typical contiguous tables.

        Args:
            spreadsheet_url_or_id: Same resolution as :meth:`read_spreadsheet`.
            column: Column letters only (e.g. ``A`` or ``AB``); not a full cell ref.
            worksheet_title: Tab when reading a non-qualified fragment.

        Returns:
            1-based row index of the last non-empty cell in that column, or ``0`` if none.

        Raises:
            ValueError: Invalid ``column`` letters.
        """
        col = column.strip().upper().replace("$", "")
        if not col or not col.isalpha():
            raise ValueError(f"column must be letters only (e.g. 'A'); got {column!r}")
        col_letters_to_index(col)
        range_name = f"{col}:{col}"
        rows = await self.read_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            worksheet_title=worksheet_title,
        )
        for i in range(len(rows) - 1, -1, -1):
            row = rows[i]
            if row and any(str(c).strip() != "" for c in row):
                return i + 1
        return 0

    async def update_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Overwrite a rectangular range (Sheets API ``spreadsheets.values.update``).

        ``range_name`` is **required** (non-empty after strip): local A1 or sheet-qualified
        range. ``values`` is a 2D list aligned with that range. ``value_input_option`` is
        passed through (e.g. ``RAW`` vs ``USER_ENTERED``).

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            range_name: A1 target range (required).
            values: Replacement cell values (required).
            value_input_option: Sheets API value input mode.
            worksheet_title: Tab when ``range_name`` is not qualified.

        Returns:
            API response dict (e.g. ``updatedRange``, ``updatedRows``, ``updatedCells``).

        Raises:
            ValueError: If ``values`` is ``None`` or ``range_name`` is blank.
        """
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
        range_name: Optional[str] = None,
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append rows after existing table data (Sheets API ``spreadsheets.values.append``).

        When ``range_name`` is ``None``, empty, or whitespace-only, the range is the **entire
        worksheet** named by ``worksheet_title`` (qualified tab only), and Google determines the
        table extent and where new rows go. Otherwise ``range_name`` is the usual **table
        anchor** (e.g. ``A1`` or ``A:C`` header columns) on that tab.

        The response often includes ``tableRange`` and ``updates.updatedRange``—use those to
        see exactly where rows were written.

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            range_name: Optional A1 anchor; omit for whole-tab append.
            values: Rows to append as a 2D list (required).
            value_input_option: Sheets API value input mode.
            worksheet_title: Tab when ``range_name`` is not sheet-qualified.

        Returns:
            Full append API response (including ``updates``, ``tableRange``, etc.).

        Raises:
            ValueError: If ``values`` is ``None`` or spreadsheet resolution fails.
        """
        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        if not worksheet_title:
            worksheet_title = self.worksheet_title

        spreadsheet_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        if values is None:
            raise ValueError("values is required")
        anchor = (range_name or "").strip()
        full_range = self._a1_range(anchor, worksheet_title)
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
        """Clear values from one or more ranges (``spreadsheets.values.batchClear``).

        Each entry in ``ranges`` may be local A1 or already qualified; blanks are skipped.
        At least one non-empty range is required after qualification.

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            ranges: List of A1 ranges to clear.
            worksheet_title: Tab used to qualify unqualified strings.

        Returns:
            The API ``batchClear`` response body.

        Raises:
            ValueError: If ``ranges`` is ``None`` or none remain after filtering.
        """
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
        """Build a ``GridRange`` dict for ``batchUpdate`` from an A1 ``range_name``."""
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
        """Apply ``userEnteredFormat`` via ``repeatCell`` (``spreadsheets.batchUpdate``).

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            range_name: Rectangular A1 range (local or qualified).
            worksheet_title: Tab when ``range_name`` is not qualified.
            user_entered_format: Nested format dict per Sheets API (fonts, colors, etc.).
            fields: Field mask for ``repeatCell``; defaults to ``userEnteredFormat``.

        Returns:
            ``batchUpdate`` response.

        Raises:
            ValueError: If ``user_entered_format`` is missing or ``range_name`` is invalid.
        """
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
        """Merge a rectangle of cells (``mergeCells`` in ``batchUpdate``).

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            range_name: Rectangular A1 range.
            worksheet_title: Tab when ``range_name`` is not qualified.
            merge_type: ``MERGE_ALL``, ``MERGE_ROWS``, or ``MERGE_COLUMNS``.

        Returns:
            ``batchUpdate`` response.

        Raises:
            ValueError: Invalid ``merge_type`` or range/metadata errors from the API layer.
        """
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
            "requests": [{"mergeCells": {"range": grid_range, "mergeType": merge_type}}]
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
        """Split merged cells in a rectangle (``unmergeCells`` in ``batchUpdate``).

        Args:
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            range_name: Rectangular A1 range covering merged cells.
            worksheet_title: Tab when ``range_name`` is not qualified.

        Returns:
            ``batchUpdate`` response.
        """
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
        """Create a new spreadsheet file (``spreadsheets.create``).

        Args:
            title: Document title (first tab may default per Google).

        Returns:
            Dict with ``spreadsheetId``, ``spreadsheetUrl``, and ``properties.title``
            (per requested fields).
        """
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

    @tool(name="google_sheets__read_spreadsheet")
    async def _t_read_spreadsheet(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[
            Optional[str], "A1-style range to read (default: entire worksheet)"
        ] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Read data from a Google Sheets spreadsheet."""
        if range_name is None:
            range_name = ""
        result = await self.read_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__update_spreadsheet")
    async def _t_update_spreadsheet(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[Optional[str], "A1-style range to update"] = None,
        values: Annotated[
            Optional[List[List[Any]]], "2D array of values to write"
        ] = None,
        value_input_option: Annotated[
            Optional[str],
            "How to interpret input data: 'RAW' or 'USER_ENTERED' (default: 'RAW')",
        ] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Update (overwrite) cells in a Google Sheets spreadsheet."""
        if range_name is None:
            range_name = ""
        if value_input_option is None:
            value_input_option = "RAW"
        result = await self.update_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__append_spreadsheet")
    async def _t_append_spreadsheet(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[
            Optional[str],
            "A1-style range to append to (default: append after last row)",
        ] = None,
        values: Annotated[
            Optional[List[List[Any]]], "2D array of values to append"
        ] = None,
        value_input_option: Annotated[
            Optional[str],
            "How to interpret input data: 'RAW' or 'USER_ENTERED' (default: 'RAW')",
        ] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Append rows of data after the last filled row in a Google Sheets spreadsheet."""  # noqa: E501
        if value_input_option is None:
            value_input_option = "RAW"
        result = await self.append_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            values=values,
            value_input_option=value_input_option,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__create_spreadsheet")
    async def _t_create_spreadsheet(
        self,
        title: Annotated[str, "Title for the new spreadsheet"],
    ) -> str:
        """Create a new Google Sheets spreadsheet."""
        result = await self.create_spreadsheet(title=title)
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__delete_spreadsheet")
    async def _t_delete_spreadsheet(
        self,
        spreadsheet_url_or_id: Annotated[Optional[str], "Spreadsheet URL or ID"] = None,
    ) -> str:
        """Permanently delete a Google Sheets spreadsheet."""
        result = await self.delete_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
        )
        return json.dumps({"deleted": result}, indent=2)

    @tool(name="google_sheets__create_worksheet")
    async def _t_create_worksheet(
        self,
        title: Annotated[str, "Title for the new worksheet"],
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        rows: Annotated[
            Optional[int], "Number of rows for the new worksheet (default: 1000)"
        ] = None,
        cols: Annotated[
            Optional[int], "Number of columns for the new worksheet (default: 26)"
        ] = None,
    ) -> str:
        """Create a new worksheet in a Google Sheets spreadsheet."""
        if rows is None:
            rows = 1000
        if cols is None:
            cols = 26
        result = await self.create_worksheet(
            title=title,
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            rows=rows,
            cols=cols,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__update_worksheet")
    async def _t_update_worksheet(
        self,
        worksheet_title: Annotated[str, "Title of the worksheet to update"],
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        new_title: Annotated[Optional[str], "New title for the worksheet"] = None,
        rows: Annotated[Optional[int], "New number of rows"] = None,
        cols: Annotated[Optional[int], "New number of columns"] = None,
        hidden: Annotated[Optional[bool], "Whether the worksheet is hidden"] = None,
        tab_color: Annotated[
            Optional[dict],
            'Tab color with RGB components 0–1, e.g. {"red": 1, "green": 0, "blue": 0}',
        ] = None,
    ) -> str:
        """Update properties of a worksheet in a Google Sheets spreadsheet."""
        result = await self.update_worksheet(
            worksheet_title=worksheet_title,
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            new_title=new_title,
            rows=rows,
            cols=cols,
            hidden=hidden,
            tab_color=tab_color,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__delete_worksheet")
    async def _t_delete_worksheet(
        self,
        worksheet_title: Annotated[str, "Title of the worksheet to delete"],
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
    ) -> str:
        """Delete a worksheet from a Google Sheets spreadsheet."""
        result = await self.delete_worksheet(
            worksheet_title=worksheet_title,
            spreadsheet_url_or_id=spreadsheet_url_or_id,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__merge_cells")
    async def _t_merge_cells(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[Optional[str], "A1-style range to merge"] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
        merge_type: Annotated[
            Optional[str],
            "Merge type: 'MERGE_ALL' or 'MERGE_ROWS' or 'MERGE_COLUMNS' (default: 'MERGE_ALL')",  # noqa: E501
        ] = None,
    ) -> str:
        """Merge a range of cells in a Google Sheets spreadsheet."""
        if range_name is None:
            range_name = ""
        if merge_type is None:
            merge_type = "MERGE_ALL"
        result = await self.merge_cells(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            worksheet_title=worksheet_title,
            merge_type=merge_type,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__unmerge_cells")
    async def _t_unmerge_cells(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[Optional[str], "A1-style range to unmerge"] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Unmerge previously merged cells in a range of a Google Sheets spreadsheet."""  # noqa: E501
        if range_name is None:
            range_name = ""
        result = await self.unmerge_cells(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__format_cells")
    async def _t_format_cells(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        range_name: Annotated[Optional[str], "A1-style range to format"] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
        user_entered_format: Annotated[
            Optional[dict],
            "Formatting specification (e.g. background color, text format)",
        ] = None,
        fields: Annotated[
            Optional[str],
            "Field mask specifying which format fields to apply (e.g. 'userEnteredFormat')",  # noqa: E501
        ] = None,
    ) -> str:
        """Apply formatting to a range of cells in a Google Sheets spreadsheet."""
        if range_name is None:
            range_name = ""
        result = await self.format_cells(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            range_name=range_name,
            worksheet_title=worksheet_title,
            user_entered_format=user_entered_format,
            fields=fields,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__last_filled_row")
    async def _t_last_filled_row(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        column: Annotated[
            Optional[str], "Column letter to check (default: 'A')"
        ] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Get the 1-based row number of the last filled cell in a column of a Google Sheets spreadsheet."""  # noqa: E501
        if column is None:
            column = "A"
        result = await self.last_filled_row_1based(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            column=column,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__batch_clear")
    async def _t_batch_clear(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        ranges: Annotated[
            Optional[List[str]], "List of A1-style ranges to clear"
        ] = None,
        worksheet_title: Annotated[
            Optional[str], "Worksheet title (default: first worksheet)"
        ] = None,
    ) -> str:
        """Clear one or more ranges of cells in a Google Sheets spreadsheet."""
        result = await self.batch_clear(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            ranges=ranges,
            worksheet_title=worksheet_title,
        )
        return json.dumps(result, indent=2)

    @tool(name="google_sheets__share_spreadsheet")
    async def _t_share_spreadsheet(
        self,
        spreadsheet_url_or_id: Annotated[
            Optional[str],
            "Spreadsheet URL or ID (default: agent's configured spreadsheet)",
        ] = None,
        share_type: Annotated[
            Optional[str], "Share type: 'link' or 'email' (default: 'link')"
        ] = None,
        link_scope: Annotated[
            Optional[str], "Link scope: 'anyone' or 'domain' (default: 'anyone')"
        ] = None,
        email: Annotated[
            Optional[str],
            "Email address to share with (required when share_type is 'email')",
        ] = None,
        role: Annotated[
            Optional[str],
            "Role to assign: 'reader', 'writer', or 'owner' (default: 'reader')",
        ] = None,
    ) -> str:
        """Share a Google Sheets spreadsheet via link or with a specific email."""
        if share_type is None:
            share_type = "link"
        if link_scope is None:
            link_scope = "anyone"
        if role is None:
            role = "reader"
        result = await self.share_spreadsheet(
            spreadsheet_url_or_id=spreadsheet_url_or_id,
            share_type=share_type,
            link_scope=link_scope,
            email=email,
            role=role,
        )
        return json.dumps(result, indent=2)

    async def create_worksheet(
        self,
        title: str,
        spreadsheet_url_or_id: Optional[str] = None,
        rows: int = 1000,
        cols: int = 26,
    ) -> Dict[str, Any]:
        """Add a worksheet (tab) to an existing spreadsheet (``addSheet``).

        Args:
            title: New tab name.
            spreadsheet_url_or_id: Parent spreadsheet URL/id or action default.
            rows: Initial row count for the grid.
            cols: Initial column count for the grid.

        Returns:
            ``batchUpdate`` response (includes replies with new sheet metadata).
        """
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
        """Update tab metadata (``updateSheetProperties`` in ``batchUpdate``).

        Provide at least one of ``new_title``, ``rows``, ``cols``, ``hidden``, ``tab_color``.

        Args:
            worksheet_title: Current tab name to update (required).
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.
            new_title: Rename the tab.
            rows: New row count (grid size).
            cols: New column count (grid size).
            hidden: Whether the tab is hidden.
            tab_color: Tab color (RGB components 0–1 per API).

        Returns:
            ``batchUpdate`` response.

        Raises:
            ValueError: Missing worksheet title, no fields to update, or tab not found.
        """
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
        """Delete a tab by title (``deleteSheet`` in ``batchUpdate``).

        Args:
            worksheet_title: Tab name to remove.
            spreadsheet_url_or_id: Target spreadsheet URL/id or action default.

        Returns:
            ``batchUpdate`` response.
        """
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
        """Create a Drive permission on the spreadsheet file (share by link or user).

        Same general idea as ``GoogleDriveAction.share_file``: uses Drive API permissions.

        Args:
            spreadsheet_url_or_id: File URL/id or action default.
            share_type: ``"link"`` for a link permission, otherwise interpreted as user invite
                when combined with ``email``.
            link_scope: Drive permission ``type`` for link sharing (e.g. ``"anyone"``).
            email: Recipient email when not using link sharing.
            role: Drive role (e.g. ``"reader"``, ``"writer"``).

        Returns:
            For link shares, ``{"webViewLink": ...}`` from ``files.get``; else ``{"success": True}``.

        Raises:
            Google API errors propagate from the client library if the call fails.
        """
        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        file_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        service = await self._get_sheets_service()

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
        """Permanently delete the spreadsheet file (Drive ``files.delete``).

        Args:
            spreadsheet_url_or_id: File URL/id or action default.

        Returns:
            ``True`` if the delete call completed without raising.

        Raises:
            Google API errors propagate from the client library if the call fails.
        """
        if not spreadsheet_url_or_id:
            spreadsheet_url_or_id = self.spreadsheet_url

        file_id = resolve_spreadsheet_id(
            self._resolve_spreadsheet_url_or_id(spreadsheet_url_or_id)
        )
        service = await self._get_sheets_service()
        service.files().delete(fileId=file_id).execute()
        return True
