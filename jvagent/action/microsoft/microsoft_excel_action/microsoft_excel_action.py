import io
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional
from urllib.parse import quote

import openpyxl
from jvspatial.core.annotations import attribute

from jvagent.action.google.google_sheets_action.google_sheets_action import (
    compose_a1_range,
    qualify_sheet_title,
    resolve_spreadsheet_id,
)

from ..microsoft_action import MicrosoftAction

logger = logging.getLogger(__name__)


def resolve_workbook_item_id(url_or_id: Optional[str]) -> str:
    """Resolve a OneDrive/SharePoint file URL or return a raw drive item id."""
    s = (url_or_id or "").strip()
    if not s:
        raise ValueError("Missing workbook id / URL")
    if "docs.google.com/spreadsheets" in s:
        return resolve_spreadsheet_id(s)
    m = re.search(r"/items/([^/?#]+)", s, re.I)
    if m:
        return m.group(1)
    return s


class MicrosoftExcelAction(MicrosoftAction):
    """Excel workbooks on OneDrive via Microsoft Graph workbook APIs."""

    worksheet_title: str = attribute(
        default="Sheet1",
        description="Default worksheet (tab) title when range has no sheet name",
    )
    spreadsheet_url: str = attribute(
        default="",
        description="Default workbook: OneDrive item id or sharing URL",
    )

    SCOPES: ClassVar[List[str]] = [
        "offline_access",
        "User.Read",
        "Files.ReadWrite.All",
    ]

    def _effective_worksheet_title(self, worksheet_title: Optional[str]) -> str:
        if worksheet_title is not None and str(worksheet_title).strip():
            return str(worksheet_title).strip()
        return self.worksheet_title

    def _resolve_workbook(self, spreadsheet_url_or_id: Optional[str] = None) -> str:
        if spreadsheet_url_or_id and str(spreadsheet_url_or_id).strip():
            return resolve_workbook_item_id(str(spreadsheet_url_or_id).strip())
        if self.spreadsheet_url and str(self.spreadsheet_url).strip():
            return resolve_workbook_item_id(str(self.spreadsheet_url).strip())
        raise ValueError(
            "Provide spreadsheet_url_or_id, or set spreadsheet_url on the MicrosoftExcelAction"
        )

    def _ws_seg(self, worksheet_title: str) -> str:
        q = qualify_sheet_title(worksheet_title or "")
        if q.startswith("'") and q.endswith("'"):
            inner = q[1:-1].replace("''", "'")
        else:
            inner = q
        inner = inner.replace("'", "''")
        return f"worksheets('{inner}')"

    def _local_a1(self, range_name: str, worksheet_title: Optional[str]) -> str:
        ws = self._effective_worksheet_title(worksheet_title)
        full = compose_a1_range(ws, range_name if range_name else None)
        if "!" in full:
            return full.split("!", 1)[1]
        return ""

    @asynccontextmanager
    async def _workbook_session(self, item_id: str) -> AsyncIterator[str]:
        sess = await self.graph_json(
            "POST",
            f"/me/drive/items/{item_id}/workbook/createSession",
            json_body={"persistChanges": True},
            ok=(200, 201),
        )
        sid = (sess or {}).get("id") or ""
        if not sid:
            raise RuntimeError("Could not create Excel workbook session")
        try:
            yield sid
        finally:
            try:
                await self.graph_request(
                    "POST",
                    f"/me/drive/items/{item_id}/workbook/closeSession",
                    json_body={"workbookSessionId": sid},
                )
            except Exception:
                logger.debug("closeSession failed", exc_info=True)

    async def read_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        worksheet_title: Optional[str] = None,
    ) -> List[List[Any]]:
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        ws = self._effective_worksheet_title(worksheet_title)
        seg = self._ws_seg(ws)
        if not range_name.strip():
            path = f"/me/drive/items/{item_id}/workbook/{seg}/usedRange"
            data = await self.graph_json("GET", path, params={"valuesOnly": "true"})
        else:
            addr = self._local_a1(range_name, worksheet_title)
            path = f"/me/drive/items/{item_id}/workbook/{seg}/range(address='{addr}')"
            data = await self.graph_json("GET", path)

        vals = (data or {}).get("values") if isinstance(data, dict) else None
        return vals or []

    async def update_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: str = "",
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        _ = value_input_option
        if values is None:
            raise ValueError("values is required")
        if not range_name.strip():
            raise ValueError("range_name is required for update")
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        ws = self._effective_worksheet_title(worksheet_title)
        seg = self._ws_seg(ws)
        addr = self._local_a1(range_name, worksheet_title)
        async with self._workbook_session(item_id) as session_id:
            path = f"/me/drive/items/{item_id}/workbook/{seg}/range(address='{addr}')"
            resp = await self.graph_request(
                "PATCH",
                path,
                json_body={"values": values},
                headers={"workbook-session-id": session_id},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(
                    f"update range failed: {resp.status_code} {resp.text[:400]}"
                )
            return {"success": True} if resp.status_code == 204 else resp.json()

    async def append_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        range_name: Optional[str] = None,
        values: Optional[List[List[Any]]] = None,
        value_input_option: str = "RAW",
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        _ = value_input_option
        if values is None:
            raise ValueError("values is required")
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        ws_default = self._effective_worksheet_title(worksheet_title)
        existing = await self.read_spreadsheet(
            spreadsheet_url_or_id=item_id,
            range_name=range_name or "",
            worksheet_title=ws_default,
        )
        start_row = len(existing) + 1
        anchor = (range_name or "A").strip() or "A"
        col_part = "".join(ch for ch in anchor.split("!")[-1] if ch.isalpha()) or "A"
        addr = f"{col_part}{start_row}"
        return await self.update_spreadsheet(
            spreadsheet_url_or_id=item_id,
            range_name=addr,
            values=values,
            worksheet_title=ws_default,
        )

    async def batch_clear(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        ranges: Optional[List[str]] = None,
        worksheet_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not ranges:
            raise ValueError("ranges is required")
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        ws = self._effective_worksheet_title(worksheet_title)
        seg = self._ws_seg(ws)
        cleared = 0
        async with self._workbook_session(item_id) as session_id:
            for r in ranges:
                r = (r or "").strip()
                if not r:
                    continue
                addr = self._local_a1(r, worksheet_title)
                path = (
                    f"/me/drive/items/{item_id}/workbook/{seg}/range(address='{addr}')"
                )
                await self.graph_request(
                    "PATCH",
                    path,
                    json_body={"values": []},
                    headers={"workbook-session-id": session_id},
                )
                cleared += 1
        return {"success": True, "clearedRanges": cleared}

    async def format_cells(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            "Excel graph format_cells is not implemented; use update with raw values."
        )

    async def merge_cells(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("Excel graph merge_cells is not implemented.")

    async def unmerge_cells(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError("Excel graph unmerge_cells is not implemented.")

    async def create_spreadsheet(self, title: str) -> Dict[str, Any]:
        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        raw = buf.read()
        safe = title.replace(":", "_").replace("/", "_") or "Workbook"
        if not safe.lower().endswith(".xlsx"):
            safe = f"{safe}.xlsx"
        path = f"/me/drive/root:/{quote(safe)}:/content"
        resp = await self.graph_request(
            "PUT",
            path,
            content=raw,
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"create workbook failed: {resp.status_code} {resp.text[:400]}"
            )
        meta = resp.json()
        iid = meta.get("id")
        return {
            "spreadsheetId": iid,
            "spreadsheetUrl": meta.get("webUrl"),
            "properties": {"title": title},
        }

    async def create_worksheet(
        self,
        title: str,
        spreadsheet_url_or_id: Optional[str] = None,
        rows: int = 1000,
        cols: int = 26,
    ) -> Dict[str, Any]:
        _ = rows
        _ = cols
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        async with self._workbook_session(item_id) as session_id:
            data = await self.graph_request(
                "POST",
                f"/me/drive/items/{item_id}/workbook/worksheets/add",
                json_body={"name": title},
                headers={"workbook-session-id": session_id},
            )
            if data.status_code not in (200, 201):
                raise RuntimeError(data.text[:400])
            return data.json() if data.content else {"success": True}

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
        if any(x is not None for x in (rows, cols, hidden, tab_color)):
            raise NotImplementedError(
                "Microsoft Excel via Graph supports only worksheet rename (new_title), "
                "not grid size / hidden / tab color."
            )
        if not new_title:
            raise ValueError(
                "Microsoft Excel: provide new_title to rename the worksheet."
            )
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        seg = self._ws_seg(worksheet_title)
        async with self._workbook_session(item_id) as session_id:
            path = f"/me/drive/items/{item_id}/workbook/{seg}"
            data = await self.graph_request(
                "PATCH",
                path,
                json_body={"name": new_title},
                headers={"workbook-session-id": session_id},
            )
            if data.status_code not in (200, 204):
                raise RuntimeError(data.text[:400])
            return {"success": True}

    async def delete_worksheet(
        self,
        worksheet_title: str,
        spreadsheet_url_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        seg = self._ws_seg(worksheet_title)
        async with self._workbook_session(item_id) as session_id:
            path = f"/me/drive/items/{item_id}/workbook/{seg}"
            resp = await self.graph_request(
                "DELETE",
                path,
                headers={"workbook-session-id": session_id},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(resp.text[:400])
            return {"success": True}

    async def share_spreadsheet(
        self,
        spreadsheet_url_or_id: Optional[str] = None,
        share_type: str = "link",
        link_scope: str = "anyone",
        email: Optional[str] = None,
        role: str = "reader",
    ) -> Dict[str, Any]:
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        if share_type == "link":
            stype = "view" if role in ("reader", "read", "view") else "edit"
            scope = "anonymous" if link_scope == "anyone" else "organization"
            data = await self.graph_json(
                "POST",
                f"/me/drive/items/{item_id}/createLink",
                json_body={"type": stype, "scope": scope},
            )
            link_url = (data or {}).get("link", {}).get("webUrl")
            if link_url:
                return {"webViewLink": link_url}
            return {"success": True, "result": data}
        if share_type == "user" and not email:
            raise ValueError("email is required for user share")
        invite_role = "read" if role in ("reader", "read") else "write"
        data = await self.graph_json(
            "POST",
            f"/me/drive/items/{item_id}/invite",
            json_body={
                "recipients": [{"email": email}],
                "roles": [invite_role],
            },
        )
        return {"success": True, "result": data}

    async def delete_spreadsheet(
        self, spreadsheet_url_or_id: Optional[str] = None
    ) -> bool:
        item_id = self._resolve_workbook(spreadsheet_url_or_id)
        await self.graph_json("DELETE", f"/me/drive/items/{item_id}", ok=(204,))
        return True

    async def get_tools(self) -> List[Any]:
        """Full Excel tool surface (ADR-0012: actions are first-class tools)."""
        import json

        from jvagent.tooling.tool import Tool

        action = self

        async def _read_spreadsheet(
            spreadsheet_url_or_id: Optional[str] = None,
            range_name: str = "",
            worksheet_title: Optional[str] = None,
        ) -> str:
            result = await action.read_spreadsheet(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                range_name=range_name,
                worksheet_title=worksheet_title,
            )
            return json.dumps(result, indent=2)

        async def _update_spreadsheet(
            spreadsheet_url_or_id: Optional[str] = None,
            range_name: str = "",
            values: Optional[List[List[Any]]] = None,
            value_input_option: str = "RAW",
            worksheet_title: Optional[str] = None,
        ) -> str:
            result = await action.update_spreadsheet(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                range_name=range_name,
                values=values,
                value_input_option=value_input_option,
                worksheet_title=worksheet_title,
            )
            return json.dumps(result, indent=2)

        async def _append_spreadsheet(
            spreadsheet_url_or_id: Optional[str] = None,
            range_name: Optional[str] = None,
            values: Optional[List[List[Any]]] = None,
            value_input_option: str = "RAW",
            worksheet_title: Optional[str] = None,
        ) -> str:
            result = await action.append_spreadsheet(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                range_name=range_name,
                values=values,
                value_input_option=value_input_option,
                worksheet_title=worksheet_title,
            )
            return json.dumps(result, indent=2)

        async def _create_spreadsheet(title: str) -> str:
            result = await action.create_spreadsheet(title=title)
            return json.dumps(result, indent=2)

        async def _delete_spreadsheet(
            spreadsheet_url_or_id: Optional[str] = None,
        ) -> str:
            result = await action.delete_spreadsheet(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
            )
            return json.dumps({"deleted": result}, indent=2)

        async def _create_worksheet(
            title: str,
            spreadsheet_url_or_id: Optional[str] = None,
            rows: int = 1000,
            cols: int = 26,
        ) -> str:
            result = await action.create_worksheet(
                title=title,
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                rows=rows,
                cols=cols,
            )
            return json.dumps(result, indent=2)

        async def _update_worksheet(
            worksheet_title: str,
            spreadsheet_url_or_id: Optional[str] = None,
            new_title: Optional[str] = None,
            rows: Optional[int] = None,
            cols: Optional[int] = None,
            hidden: Optional[bool] = None,
            tab_color: Optional[str] = None,
        ) -> str:
            result = await action.update_worksheet(
                worksheet_title=worksheet_title,
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                new_title=new_title,
                rows=rows,
                cols=cols,
                hidden=hidden,
                tab_color=tab_color,
            )
            return json.dumps(result, indent=2)

        async def _delete_worksheet(
            worksheet_title: str,
            spreadsheet_url_or_id: Optional[str] = None,
        ) -> str:
            result = await action.delete_worksheet(
                worksheet_title=worksheet_title,
                spreadsheet_url_or_id=spreadsheet_url_or_id,
            )
            return json.dumps(result, indent=2)

        async def _batch_clear(
            spreadsheet_url_or_id: Optional[str] = None,
            ranges: Optional[List[str]] = None,
            worksheet_title: Optional[str] = None,
        ) -> str:
            result = await action.batch_clear(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                ranges=ranges,
                worksheet_title=worksheet_title,
            )
            return json.dumps(result, indent=2)

        async def _share_spreadsheet(
            spreadsheet_url_or_id: Optional[str] = None,
            share_type: str = "link",
            link_scope: str = "anyone",
            email: Optional[str] = None,
            role: str = "reader",
        ) -> str:
            result = await action.share_spreadsheet(
                spreadsheet_url_or_id=spreadsheet_url_or_id,
                share_type=share_type,
                link_scope=link_scope,
                email=email,
                role=role,
            )
            return json.dumps(result, indent=2)

        return [
            Tool(
                name="excel__read_spreadsheet",
                description="Read data from an Excel spreadsheet.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "range_name": {
                            "type": "string",
                            "description": "A1 notation range (e.g. 'A1:D10'). Default: empty (reads entire worksheet)",
                        },
                        "worksheet_title": {
                            "type": "string",
                            "description": "Worksheet title to read from",
                        },
                    },
                    "required": [],
                },
                execute=_read_spreadsheet,
            ),
            Tool(
                name="excel__update_spreadsheet",
                description="Update (overwrite) values in an Excel spreadsheet range.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "range_name": {
                            "type": "string",
                            "description": "A1 notation range (e.g. 'A1:D10'). Default: empty",
                        },
                        "values": {
                            "type": "array",
                            "items": {"type": "array", "items": {}},
                            "description": "2D array of values to write",
                        },
                        "value_input_option": {
                            "type": "string",
                            "description": "How to interpret input values: 'RAW' or 'USER_ENTERED'. Default: 'RAW'",
                        },
                        "worksheet_title": {
                            "type": "string",
                            "description": "Worksheet title to update",
                        },
                    },
                    "required": [],
                },
                execute=_update_spreadsheet,
            ),
            Tool(
                name="excel__append_spreadsheet",
                description="Append rows of data after the last row of data in an Excel spreadsheet.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "range_name": {
                            "type": "string",
                            "description": "A1 notation range to append to (determines the table). Default: None",
                        },
                        "values": {
                            "type": "array",
                            "items": {"type": "array", "items": {}},
                            "description": "2D array of values to append",
                        },
                        "value_input_option": {
                            "type": "string",
                            "description": "How to interpret input values: 'RAW' or 'USER_ENTERED'. Default: 'RAW'",
                        },
                        "worksheet_title": {
                            "type": "string",
                            "description": "Worksheet title to append to",
                        },
                    },
                    "required": [],
                },
                execute=_append_spreadsheet,
            ),
            Tool(
                name="excel__create_spreadsheet",
                description="Create a new Excel spreadsheet (workbook).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Title for the new spreadsheet",
                        },
                    },
                    "required": ["title"],
                },
                execute=_create_spreadsheet,
            ),
            Tool(
                name="excel__delete_spreadsheet",
                description="Delete an entire Excel spreadsheet (workbook).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID to delete",
                        },
                    },
                    "required": [],
                },
                execute=_delete_spreadsheet,
            ),
            Tool(
                name="excel__create_worksheet",
                description="Create a new worksheet in an Excel spreadsheet.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Title for the new worksheet",
                        },
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID to add the worksheet to",
                        },
                        "rows": {
                            "type": "integer",
                            "description": "Number of rows for the new worksheet. Default: 1000",
                        },
                        "cols": {
                            "type": "integer",
                            "description": "Number of columns for the new worksheet. Default: 26",
                        },
                    },
                    "required": ["title"],
                },
                execute=_create_worksheet,
            ),
            Tool(
                name="excel__update_worksheet",
                description="Update properties of a worksheet (rename, resize, hide, color).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "worksheet_title": {
                            "type": "string",
                            "description": "Current title of the worksheet to update",
                        },
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "new_title": {
                            "type": "string",
                            "description": "New title for the worksheet",
                        },
                        "rows": {
                            "type": "integer",
                            "description": "New number of rows",
                        },
                        "cols": {
                            "type": "integer",
                            "description": "New number of columns",
                        },
                        "hidden": {
                            "type": "boolean",
                            "description": "Whether the worksheet should be hidden",
                        },
                        "tab_color": {
                            "type": "string",
                            "description": "Tab color as a hex string (e.g. '#FF0000')",
                        },
                    },
                    "required": ["worksheet_title"],
                },
                execute=_update_worksheet,
            ),
            Tool(
                name="excel__delete_worksheet",
                description="Delete a worksheet from an Excel spreadsheet.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "worksheet_title": {
                            "type": "string",
                            "description": "Title of the worksheet to delete",
                        },
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                    },
                    "required": ["worksheet_title"],
                },
                execute=_delete_worksheet,
            ),
            Tool(
                name="excel__batch_clear",
                description="Clear one or more ranges of values in an Excel spreadsheet.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "ranges": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of A1 notation ranges to clear",
                        },
                        "worksheet_title": {
                            "type": "string",
                            "description": "Worksheet title to clear ranges from",
                        },
                    },
                    "required": [],
                },
                execute=_batch_clear,
            ),
            Tool(
                name="excel__share_spreadsheet",
                description="Share an Excel spreadsheet via link or with a specific email.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_url_or_id": {
                            "type": "string",
                            "description": "Spreadsheet URL or ID",
                        },
                        "share_type": {
                            "type": "string",
                            "description": "Type of sharing: 'link' or 'email'. Default: 'link'",
                        },
                        "link_scope": {
                            "type": "string",
                            "description": "Link scope when share_type is 'link': 'anyone' or 'domain'. Default: 'anyone'",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email address when share_type is 'email'",
                        },
                        "role": {
                            "type": "string",
                            "description": "Role to assign: 'reader', 'writer', or 'owner'. Default: 'reader'",
                        },
                    },
                    "required": [],
                },
                execute=_share_spreadsheet,
            ),
        ]
