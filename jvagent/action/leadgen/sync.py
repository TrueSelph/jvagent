"""MCP sync engine for LeadGenAction."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DIGEST_KEY = "_leadgen_sync_digest"
LEGACY_DIGEST_KEY = "_lead_sync_mcp_DIGEST"
UTC_MINUS_4 = timezone(timedelta(hours=-4))


def _now_utc_minus_4() -> str:
    return datetime.now(UTC_MINUS_4).strftime("%Y-%m-%d %H:%M")


def compute_digest(data: Dict[str, Any]) -> str:
    """Digest of the lead's real fields.

    Internal, underscore-prefixed keys (notably the stored sync digest itself)
    are excluded — otherwise the digest changes the moment it is written back
    onto the profile, so the unchanged-data check never matches and every
    capture re-syncs (duplicate rows on append-style destinations).
    """
    fields = {k: v for k, v in data.items() if not k.startswith("_")}
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_stored_digest(profile_data: Dict[str, Any]) -> Optional[str]:
    return profile_data.get(DIGEST_KEY) or profile_data.get(LEGACY_DIGEST_KEY)


def substitute(val: Any, profile_data: Dict[str, Any], user_id: str) -> Any:
    # Exclude internal, underscore-prefixed keys (e.g. the stored sync digest)
    # from every template — they are bookkeeping, not lead data, and must not
    # leak into a synced destination. Matches profile_keys/profile_row.
    public = {k: v for k, v in profile_data.items() if not k.startswith("_")}
    profile_json = json.dumps(public, default=str)
    profile_keys = sorted(public)
    profile_row = [str(public.get(k, "")) for k in profile_keys]

    def _replace(s: str) -> str:
        s = s.replace("{user_id}", str(user_id))
        s = s.replace("{profile_json}", profile_json)
        s = s.replace("{profile_keys}", json.dumps(profile_keys))
        s = s.replace("{profile_row}", json.dumps(profile_row))
        s = s.replace("{last_updated}", _now_utc_minus_4())
        for k, v in profile_data.items():
            s = s.replace(f"{{{k}}}", str(v) if v is not None else "")
        # Any remaining {placeholder} refers to a field that has not been
        # captured; keep the cell blank instead of leaking the literal token.
        s = __import__("re").sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", s)
        return s

    if isinstance(val, str):
        replaced = _replace(val)
        try:
            parsed = json.loads(replaced)
            if isinstance(parsed, (list, dict)):
                return substitute(parsed, profile_data, user_id)
        except (json.JSONDecodeError, ValueError):
            pass
        return replaced
    if isinstance(val, list):
        return [substitute(item, profile_data, user_id) for item in val]
    if isinstance(val, dict):
        return {k: substitute(v, profile_data, user_id) for k, v in val.items()}
    return val


def sync_threshold_met(
    profile_data: Dict[str, Any],
    min_fields: List[str],
    require_any: List[str],
) -> bool:
    for field in min_fields:
        val = profile_data.get(field)
        if val is None or (isinstance(val, str) and not str(val).strip()):
            return False
    if require_any:
        for field in require_any:
            val = profile_data.get(field)
            if val is not None and str(val).strip():
                return True
        return False
    return True


async def sync_to_destinations(
    action: Any,
    destinations: List[Dict[str, Any]],
    profile_data: Dict[str, Any],
    user_id: str,
) -> Tuple[Dict[str, str], bool]:
    if not destinations:
        return {}, False

    digest = compute_digest(profile_data)
    last_digest = get_stored_digest(profile_data)
    if digest == last_digest:
        return {"_digest": "unchanged"}, False

    # Resolve the MCP gateway once. A missing/disabled MCPAction, or a
    # destination that names an unregistered server, degrades to a graceful
    # skip rather than an error — leadgen never blocks the conversation on an
    # unconfigured connector.
    try:
        mcp_action = await action.get_action("MCPAction")
    except Exception as exc:
        logger.debug("leadgen sync: MCPAction lookup failed: %s", exc)
        mcp_action = None
    configured = set(mcp_action.get_server_names()) if mcp_action else set()

    results: Dict[str, str] = {}
    any_success = False

    for entry in destinations:
        server_name = (entry.get("server") or "").strip()
        if not server_name:
            continue
        mode = (entry.get("mode") or "mcp").strip().lower()
        if mode != "mcp":
            results[server_name] = f"Unknown mode '{mode}'"
            continue
        if server_name not in configured:
            results[server_name] = "skipped: connector not configured"
            continue
        ok, msg = await _sync_mcp(mcp_action, entry, profile_data, user_id)
        results[server_name] = "ok" if ok else msg
        if ok:
            any_success = True

    return results, any_success


async def _sync_mcp(
    mcp_action: Any,
    entry: Dict[str, Any],
    profile_data: Dict[str, Any],
    uid: str,
) -> Tuple[bool, str]:
    server_name = (entry.get("server") or "").strip()
    tool_name = (entry.get("tool") or "").strip()
    raw_args = entry.get("arguments") or {}
    upsert_key = (entry.get("upsert_key") or "").strip()
    read_tool = (entry.get("read_tool") or "").strip()
    write_tool = (entry.get("write_tool") or "").strip()

    if not tool_name:
        return False, f"Missing 'tool' for MCP entry '{server_name}'."

    resolved_args = substitute(raw_args, profile_data, uid)

    try:
        client = await mcp_action.get_client_for_user(server_name, uid)
    except Exception as exc:
        try:
            client = mcp_action.get_client(server_name)
        except Exception as exc2:
            return False, f"Cannot get MCP client for '{server_name}': {exc2}"
        logger.debug("sync fallback client for %s: %s", server_name, exc)

    # Upsert path: read sheet, find existing row by key, overwrite or append.
    if upsert_key:
        if not read_tool or not write_tool:
            return (
                False,
                f"upsert_key requires read_tool and write_tool for '{server_name}'.",
            )
        ok, msg = await _sync_mcp_upsert(
            client,
            tool_name,
            read_tool,
            write_tool,
            resolved_args,
            raw_args,
            upsert_key,
            profile_data,
            uid,
        )
        return ok, msg

    try:
        call_result = await client.call_tool(tool_name, resolved_args)
        from jvagent.action.mcp.mcp_action import normalize_call_result

        norm = normalize_call_result(call_result, tool_name)
        if norm.is_error:
            return False, f"MCP error: {norm.text}"
        return True, "ok"
    except Exception as exc:
        import traceback

        logger.error(
            "sync_mcp %s.%s failed:\n%s", server_name, tool_name, traceback.format_exc()
        )
        return False, f"Exception: {exc}"


async def _sync_mcp_upsert(
    client: Any,
    append_tool: str,
    read_tool: str,
    write_tool: str,
    resolved_args: Dict[str, Any],
    raw_args: Dict[str, Any],
    upsert_key: str,
    profile_data: Dict[str, Any],
    uid: str,
) -> Tuple[bool, str]:
    """Append-or-update any MCP destination keyed by upsert_key (e.g. user_id).

    Generic strategy:
      1. Read the destination via read_tool.
      2. Search the response text for the upsert_key value.
      3. If found → call write_tool with resolved_args (augmented with
         match context like row index or old text block).
      4. If not found → call append_tool with resolved_args.
    """
    from jvagent.action.mcp.mcp_action import normalize_call_result

    key_value = str(profile_data.get(upsert_key, uid))
    if not key_value:
        return False, "upsert: upsert key value is empty"

    # Build read args from the resolved args — pass through everything
    # the destination needs (account, spreadsheetId, documentId, etc.).
    read_args = dict(resolved_args)
    # Remove append/write-specific keys that don't belong in a read call.
    for drop in (
        "values",
        "textToAppend",
        "valueInputOption",
        "addNewlineIfNeeded",
        "oldText",
        "newText",
    ):
        read_args.pop(drop, None)
    # Expand single-cell ranges (e.g. "Leads!A1") to full-column reads
    # so the upsert can find existing rows.
    if "range" in read_args:
        r = read_args["range"]
        if "!" in r:
            sheet, cell = r.split("!", 1)
            if re.match(r"^[A-Z]+\d+$", cell):
                read_args["range"] = f"{sheet}!A:Z"

    try:
        read_result = await client.call_tool(read_tool, read_args)
        norm = normalize_call_result(read_result, read_tool)
        if norm.is_error:
            return False, f"upsert read error: {norm.text}"
        response_text = norm.text
    except Exception as exc:
        import traceback

        logger.error("upsert read failed:\n%s", traceback.format_exc())
        return False, f"upsert read exception: {exc}"

    # Auto-create header row if the sheet is empty.
    rows = _parse_read_response(response_text)
    if not rows or (len(rows) == 1 and all(not cell for cell in rows[0])):
        header = _extract_header(raw_args)
        if header:
            await _write_header(client, write_tool, resolved_args, header)
            # Re-read after writing header so match_ctx has the right row count.
            try:
                read_result = await client.call_tool(read_tool, read_args)
                norm = normalize_call_result(read_result, read_tool)
                if not norm.is_error:
                    response_text = norm.text
            except Exception:
                pass

    match_ctx = _search_for_key(response_text, upsert_key, key_value)
    if match_ctx is None:
        try:
            call_result = await client.call_tool(append_tool, resolved_args)
            norm = normalize_call_result(call_result, append_tool)
            if norm.is_error:
                return False, f"upsert append error: {norm.text}"
            return True, "ok"
        except Exception as exc:
            import traceback

            logger.error("upsert append failed:\n%s", traceback.format_exc())
            return False, f"upsert append exception: {exc}"

    write_args = dict(resolved_args)
    write_args.update(match_ctx)
    # Remove append-only keys that don't belong in a write call.
    for drop in ("addNewlineIfNeeded", "textToAppend"):
        write_args.pop(drop, None)
    # If match_ctx provided oldText (doc edit), set newText from the template.
    if "oldText" in match_ctx and "newText" not in match_ctx:
        write_args["newText"] = resolved_args.get("textToAppend", "").strip()

    try:
        write_result = await client.call_tool(write_tool, write_args)
        norm = normalize_call_result(write_result, write_tool)
        if norm.is_error:
            return False, f"upsert write error: {norm.text}"
        return True, "updated"
    except Exception as exc:
        import traceback

        logger.error("upsert write failed:\n%s", traceback.format_exc())
        return False, f"upsert write exception: {exc}"


def _search_for_key(text: str, key: str, value: str) -> Optional[Dict[str, Any]]:
    """Search response text for a key=value match. Returns match context or None.

    Tries multiple strategies in order:
      1. JSON with 'values' array (spreadsheet rows) → returns {"range": "Sheet!A5:Z5"}
      2. Markdown Row N: [...] lines → returns {"range": "Sheet!A5:Z5"}
      3. ---delimited text blocks → returns {"oldText": "<block>"}
      4. Plain text substring match → returns {} (key exists, write tool handles the rest)
    """
    if not text or not value:
        return None

    # Strip <untrusted-content> wrappers that some MCP servers (e.g.
    # google-workspace-mcp) wrap around all responses.
    # Extract sheet name from the full text before stripping — the range
    # info lives outside the <untrusted-content> block.
    sheet = "Sheet1"
    range_match = re.search(
        r"(?:Spreadsheet\s+)?[Rr]ange:\s*\*?\*?\s*([A-Za-z0-9_]+)!", text
    )
    if range_match:
        sheet = range_match.group(1)
    text = _strip_untrusted_wrapper(text)

    # Strategy 1 & 2: row-based (spreadsheets)
    rows = _parse_read_response(text)
    if rows:
        for i, row in enumerate(rows):
            if row and str(row[0]) == value:
                row_num = i + 1
                num_cols = max(len(row), 1)
                end_col = _column_letter(num_cols)
                return {
                    "range": f"{sheet}!A{row_num}:{end_col}{row_num}",
                }
        # Rows were parsed but no match — key not found.
        return None

    # Strategy 3: ---delimited text blocks
    blocks = re.split(r"\n---\n", text)
    for block in blocks:
        block_stripped = block.strip()
        if not block_stripped:
            continue
        escaped_key = re.escape(key)
        escaped_val = re.escape(value)
        if re.search(
            rf"{escaped_key}[:\s]+{escaped_val}", block_stripped, re.IGNORECASE
        ):
            return {"oldText": block_stripped}

    # Strategy 4: plain text substring match
    if value in text:
        return {}

    return None


def _strip_untrusted_wrapper(text: str) -> str:
    """Strip <untrusted-content> wrappers that some MCP servers wrap around responses."""
    if not text:
        return ""
    match = re.search(
        r"<untrusted-content>\s*(.*?)\s*</untrusted-content>", text, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_read_response(text: str) -> List[List[str]]:
    """Extract rows from an MCP spreadsheet read response.

    Some MCP servers (e.g. google-workspace-mcp) return a markdown-wrapped
    block containing rows like:

        Row 1: ["a", "b"]
        Row 2: ["c", "d"]

    Others return plain JSON with a "values" array. Try JSON first, then
    fall back to parsing the markdown row lines.
    """
    if not text or not text.strip():
        return []
    stripped = text.strip()
    # Try plain JSON first.
    try:
        data = json.loads(stripped)
        values = data.get("values") if isinstance(data, dict) else None
        if isinstance(values, list):
            return values
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: extract Row N: [...] arrays from markdown text.
    rows: List[List[Any]] = []
    # Match lines like: Row 1: ["a", "b"] or Row 10: []
    row_re = re.compile(r"^Row\s+(\d+):\s*(\[.*\])\s*$", re.MULTILINE)
    for match in row_re.finditer(stripped):
        row_text = match.group(2)
        try:
            row = json.loads(row_text)
            if isinstance(row, list):
                rows.append(row)
        except (json.JSONDecodeError, ValueError):
            continue
    return rows


def _column_letter(n: int) -> str:
    """Convert 1-based column index to Excel letter(s)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("A") + r) + out
    return out or "A"


def _extract_header(resolved_args: Dict[str, Any]) -> Optional[List[str]]:
    """Derive column headers from the template values.

    If the values contain {placeholder} tokens, use the placeholder names
    as headers. Otherwise return None (no auto-header needed).
    """
    values = resolved_args.get("values")
    if not values or not isinstance(values, list):
        return None
    row = values[0] if isinstance(values[0], list) else values
    headers = []
    for cell in row:
        cell_str = str(cell) if cell is not None else ""
        match = re.match(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$", cell_str)
        if match:
            headers.append(match.group(1).replace("_", " ").title())
        else:
            return None
    return headers if headers else None


async def _write_header(
    client: Any,
    write_tool: str,
    resolved_args: Dict[str, Any],
    header: List[str],
) -> None:
    """Write a header row to the sheet."""

    sheet_range = resolved_args.get("range", "Sheet1!A1")
    sheet = sheet_range.split("!")[0] if "!" in sheet_range else "Sheet1"
    end_col = _column_letter(len(header))
    header_range = f"{sheet}!A1:{end_col}1"
    header_args = {
        "account": resolved_args.get("account"),
        "spreadsheetId": resolved_args.get("spreadsheetId"),
        "range": header_range,
        "values": [header],
        "valueInputOption": resolved_args.get("valueInputOption", "RAW"),
    }
    try:
        await client.call_tool(write_tool, header_args)
    except Exception as exc:
        logger.warning("upsert: failed to write header row: %s", exc)
