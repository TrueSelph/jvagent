"""MCP sync engine for LeadGenAction."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DIGEST_KEY = "_leadgen_sync_digest"
LEGACY_DIGEST_KEY = "_lead_sync_mcp_DIGEST"


def compute_digest(data: Dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_stored_digest(profile_data: Dict[str, Any]) -> Optional[str]:
    return profile_data.get(DIGEST_KEY) or profile_data.get(LEGACY_DIGEST_KEY)


def substitute(val: Any, profile_data: Dict[str, Any], user_id: str) -> Any:
    profile_json = json.dumps(profile_data, default=str)
    profile_keys = sorted(k for k in profile_data if not k.startswith("_"))
    profile_row = [str(profile_data.get(k, "")) for k in profile_keys]

    def _replace(s: str) -> str:
        s = s.replace("{user_id}", str(user_id))
        s = s.replace("{profile_json}", profile_json)
        s = s.replace("{profile_keys}", json.dumps(profile_keys))
        s = s.replace("{profile_row}", json.dumps(profile_row))
        for k, v in profile_data.items():
            s = s.replace(f"{{{k}}}", str(v) if v is not None else "")
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
        ok, msg = await _sync_mcp(action, entry, profile_data, user_id)
        results[server_name] = "ok" if ok else msg
        if ok:
            any_success = True

    return results, any_success


async def _sync_mcp(
    action: Any,
    entry: Dict[str, Any],
    profile_data: Dict[str, Any],
    uid: str,
) -> Tuple[bool, str]:
    server_name = (entry.get("server") or "").strip()
    tool_name = (entry.get("tool") or "").strip()
    raw_args = entry.get("arguments") or {}

    if not tool_name:
        return False, f"Missing 'tool' for MCP entry '{server_name}'."

    try:
        mcp_action = await action.get_action("MCPAction")
    except Exception as exc:
        return False, f"MCPAction not found: {exc}"

    if mcp_action is None:
        return False, "MCPAction is not enabled on this agent."

    resolved_args = substitute(raw_args, profile_data, uid)

    try:
        client = await mcp_action.get_client_for_user(server_name, uid)
    except Exception as exc:
        try:
            client = mcp_action.get_client(server_name)
        except Exception as exc2:
            return False, f"Cannot get MCP client for '{server_name}': {exc2}"
        logger.debug("sync fallback client for %s: %s", server_name, exc)

    try:
        call_result = await client.call_tool(tool_name, resolved_args)
        from jvagent.action.mcp.mcp_action import _normalize_call_result

        norm = _normalize_call_result(call_result, tool_name)
        if norm.is_error:
            return False, f"MCP error: {norm.text}"
        return True, "ok"
    except Exception as exc:
        logger.error("sync_mcp %s.%s: %s", server_name, tool_name, exc)
        return False, f"Exception: {exc}"
