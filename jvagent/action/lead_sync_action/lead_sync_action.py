"""Lead Sync Action.

Exposes a single `sync_lead` tool that reads the full lead profile and pushes it to
every destination configured under `sync_servers` in agent.yaml.

All sync destinations use MCP (stdio servers). Template variables supported in
arguments values:
    {user_id}        – the user's unique ID
    {profile_json}   – the full profile as a JSON string
    {profile_keys}   – sorted list of profile field names (for sheet headers)
    {profile_row}    – flat list of profile values matching {profile_keys} order
    {<field_key>}    – any top-level key from the profile YAML

agent.yaml example
------------------
- action: jvagent/lead_sync_action
  context:
    enabled: true
    sync_servers:
      - server: google_sheets
        mode: mcp
        tool: sheets_append_values
        arguments:
          spreadsheetId: "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
          range: "Leads"
          values:
            - "{profile_row}"

      - server: sqlite_db
        mode: mcp
        tool: write_query
        arguments:
          query: "INSERT INTO leads (user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data"
          parameters:
            - "{user_id}"
            - "{profile_json}"
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List

from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool
from jvagent.tooling.tool_executor import get_dispatch_visitor
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)

_DIGEST_KEY = "_lead_sync_mcp_DIGEST"


def _compute_digest(data: Dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _substitute(val: Any, profile_data: Dict[str, Any], user_id: str) -> Any:
    """Recursively replace {placeholder} tokens in argument values.

    Special tokens:
        {user_id}       – the user's unique ID
        {profile_json}  – the full profile as a JSON string
        {profile_keys}  – sorted list of profile field names
        {profile_row}   – flat list of values matching {profile_keys} order
        {<field_key>}   – any top-level key from the profile
    """
    # Pre-compute the special tokens once so nested structures reuse them.
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
                return _substitute(parsed, profile_data, user_id)
        except (json.JSONDecodeError, ValueError):
            pass
        return replaced
    if isinstance(val, list):
        return [_substitute(item, profile_data, user_id) for item in val]
    if isinstance(val, dict):
        return {k: _substitute(v, profile_data, user_id) for k, v in val.items()}
    return val


class LeadSyncAction(Action):
    """Sync the full lead profile to all configured MCP destinations.

    Each sync_servers entry calls an MCP stdio server/tool directly.
    """

    description: str = (
        "Sync the full lead profile to all active external systems via MCP."
    )

    sync_servers: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "List of sync targets. Each entry: {server, mode, ...}. "
            "mode='mcp' calls an MCP stdio server tool. "
            "Arguments support {user_id}, {profile_json}, and {<field_key>} placeholders."
        ),
    )

    # ── Tool handler ──────────────────────────────────────────────────────────
    @tool(
        name="sync_lead",
        description=(
            "Sync the current lead profile to all active external systems "
            "(Google Sheets, databases, CRMs, etc.). "
            "Call this immediately after lead_profile__save returns 'updated'. "
            "Safe to call multiple times — skips if nothing has changed."
        ),
    )
    async def _tool_sync(self, **kwargs: Any) -> str:
        visitor = get_dispatch_visitor()
        interaction = getattr(visitor, "interaction", None)
        if not interaction:
            logger.warning("sync_lead: No active interaction found.")
            return json.dumps({"error": "No active interaction."})

        user = await interaction.get_user()
        if not user:
            logger.warning("sync_lead: No user found on interaction.")
            return json.dumps({"error": "No user found."})

        uid = user.user_id

        # ── Load full profile ─────────────────────────────────────────────────
        profile_data: Dict[str, Any] = {}
        lead_profile_obj: Any = None
        try:
            from jvagent.action.lead_profile import LeadProfile
            lead_profile_obj = await LeadProfile.get_or_create_for_user(user)
            profile_data = lead_profile_obj.get_yaml() or {}
        except Exception as exc:
            logger.debug("LeadProfile unavailable (%s); falling back to User.memory", exc)
            try:
                mem = getattr(user, "memory", None) or {}
                if isinstance(mem, dict):
                    profile_data = dict(mem)
            except Exception:
                pass

        if not profile_data:
            logger.info("sync_lead: skipping sync, profile data is empty for user %s", uid)
            return json.dumps({"status": "no-op", "reason": "Profile is empty."})

        # ── Digest dedup ──────────────────────────────────────────────────────
        digest = _compute_digest(profile_data)
        last_digest = profile_data.get(_DIGEST_KEY)
        if digest == last_digest:
            logger.info("sync_lead: skipping sync, digest is unchanged for user %s", uid)
            return json.dumps({"status": "no-op", "reason": "Profile unchanged since last sync."})

        if not self.sync_servers:
            logger.info("sync_lead: skipping sync, no sync_servers configured for user %s", uid)
            return json.dumps({"status": "no-op", "reason": "No sync_servers configured."})

        logger.info("sync_lead: initiating sync to %d configured destinations for user %s", len(self.sync_servers), uid)

        # ── Dispatch to each configured destination ───────────────────────────
        results: Dict[str, str] = {}
        any_success = False

        for entry in self.sync_servers:
            server_name = (entry.get("server") or "").strip()
            mode = (entry.get("mode") or "mcp").strip().lower()

            if not server_name:
                continue

            if mode == "mcp":
                logger.info("sync_lead: syncing via mcp mode (server=%s, tool=%s) for user %s", server_name, entry.get("tool"), uid)
                ok, msg = await self._sync_mcp(entry, profile_data, uid)
            else:
                ok, msg = False, f"Unknown mode '{mode}'. Only 'mcp' is supported."

            results[server_name] = "ok" if ok else msg
            if ok:
                any_success = True

        logger.info("sync_lead: sync process completed for user %s with results: %s", uid, results)

        # ── Persist digest so next call skips if unchanged ────────────────────
        if any_success and lead_profile_obj is not None:
            try:
                updated = profile_data.copy()
                updated[_DIGEST_KEY] = digest
                await lead_profile_obj.set_yaml(updated)
            except Exception as exc:
                logger.debug("sync_lead: failed to persist digest: %s", exc)

        return json.dumps({"status": "sync-complete", "results": results})

    # ── MCP mode (any stdio MCP server) ───────────────────────────────────────

    async def _sync_mcp(
        self, entry: Dict[str, Any], profile_data: Dict[str, Any], uid: str
    ) -> tuple[bool, str]:
        """Call an MCP stdio server tool with substituted arguments."""
        server_name = (entry.get("server") or "").strip()
        tool_name = (entry.get("tool") or "").strip()
        raw_args = entry.get("arguments") or {}

        if not tool_name:
            return False, f"Missing 'tool' for MCP entry '{server_name}'."

        mcp_action: Any = None
        try:
            mcp_action = await self.get_action("MCPAction")
        except Exception as exc:
            return False, f"MCPAction not found: {exc}"

        if mcp_action is None:
            return False, "MCPAction is not enabled on this agent."

        resolved_args = _substitute(raw_args, profile_data, uid)

        try:
            client = await mcp_action.get_client_for_user(server_name, uid)
        except Exception as exc:
            logger.warning(
                "sync_lead[mcp]: get_client_for_user failed (server=%s); falling back: %s",
                server_name, exc,
            )
            try:
                client = mcp_action.get_client(server_name)
            except Exception as exc2:
                return False, f"Cannot get MCP client for '{server_name}': {exc2}"

        try:
            logger.info("sync_lead[mcp]: calling %s.%s for user %s", server_name, tool_name, uid)
            call_result = await client.call_tool(tool_name, resolved_args)

            from jvagent.action.mcp.mcp_action import _normalize_call_result
            norm = _normalize_call_result(call_result, tool_name)
            if norm.is_error:
                logger.warning("sync_lead[mcp]: %s.%s error: %s", server_name, tool_name, norm.text)
                return False, f"MCP error: {norm.text}"
            return True, "ok"
        except Exception as exc:
            logger.error("sync_lead[mcp]: exception on %s.%s: %s", server_name, tool_name, exc)
            return False, f"Exception: {exc}"