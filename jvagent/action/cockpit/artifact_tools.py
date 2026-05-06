"""Artifact harness tools for cockpit (session-scoped structured data on Interaction).

Artifacts let the model persist intermediate results — full documents, large
tool outputs, image interpretations, file listings — within the current
interaction so they can be retrieved later in the same task without
reprocessing or re-fetching.

Storage lives on ``Interaction.artifacts`` (a dict keyed by user-supplied
``key``). Each entry is::

    {
        "data": "<string or JSON-stringified payload>",
        "tags": ["tag1", "tag2"],
        "created_at": "2025-01-15T...",
        "updated_at": "2025-01-15T...",
        "source": "cockpit"
    }

**Read/write asymmetry.** Writes (``artifact_add`` / ``artifact_update`` /
``artifact_delete``) only affect the **current** interaction — the lifetime of
an artifact is bound to the interaction it was created in, and pruning happens
automatically via ``Conversation.interaction_limit``. Reads
(``artifact_get`` / ``artifact_search``) span **all interactions in the
current conversation** so the model can find artifacts it (or a prior
interaction) saved earlier in the session. When the same key exists in
multiple interactions, the most recent wins.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interaction_artifacts(ctx: CockpitContext) -> Optional[Dict[str, Dict[str, Any]]]:
    """Resolve the artifacts dict on the current interaction (creating if needed)."""
    interaction = getattr(ctx, "interaction", None)
    if interaction is None:
        return None
    artifacts = getattr(interaction, "artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        try:
            interaction.artifacts = artifacts
        except Exception:
            return None
    return artifacts


def _normalize_tags(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _normalize_data(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, indent=2, default=str)
    except Exception:
        return str(raw)


def _summarize(
    entry: Dict[str, Any], key: str, source_iid: Optional[str] = None
) -> str:
    data = str(entry.get("data", ""))
    preview = data if len(data) <= _PREVIEW_CHARS else data[:_PREVIEW_CHARS] + "..."
    tags = entry.get("tags") or []
    tag_suffix = f" [tags: {', '.join(tags)}]" if tags else ""
    src_suffix = f" (from prior interaction {source_iid[:8]}...)" if source_iid else ""
    return f"- {key}{tag_suffix}{src_suffix}: {preview}"


async def _scan_conversation_artifacts(
    ctx: CockpitContext,
) -> List[Tuple[str, Dict[str, Any], bool]]:
    """Yield (key, entry, is_current_interaction) across all interactions.

    Newest-first ordering, including the current in-progress interaction. Only
    used for read-side tools (``artifact_get`` / ``artifact_search``).
    """
    out: List[Tuple[str, Dict[str, Any], bool]] = []
    current_id = getattr(getattr(ctx, "interaction", None), "id", None)

    # Current interaction first (its mutations may not be persisted yet).
    artifacts = _interaction_artifacts(ctx)
    if artifacts:
        for key, entry in artifacts.items():
            out.append((key, entry, True))

    # Walk past interactions newest-first.
    conversation = getattr(ctx, "conversation", None)
    if conversation is None:
        return out
    try:
        past = await conversation.get_interactions(reverse=True)
    except Exception as exc:
        logger.debug("artifact scan: get_interactions failed: %s", exc)
        return out

    for interaction in past:
        iid = getattr(interaction, "id", None)
        if iid == current_id:
            continue  # already covered by the current-interaction pass
        ia = getattr(interaction, "artifacts", None)
        if not isinstance(ia, dict):
            continue
        for key, entry in ia.items():
            out.append((key, entry, False))

    return out


def _build_artifact_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose artifact CRUD to the cockpit model."""

    async def _add(key: str, data: Any, tags: Any = None) -> str:
        artifacts = _interaction_artifacts(ctx)
        if artifacts is None:
            return "Error: no interaction available for artifact storage."
        if not key or not str(key).strip():
            return "Error: 'key' is required and cannot be empty."
        key = str(key).strip()
        if key in artifacts:
            return (
                f"Error: artifact '{key}' already exists. "
                "Use artifact_update to overwrite, or artifact_delete first."
            )
        now = _now_iso()
        artifacts[key] = {
            "data": _normalize_data(data),
            "tags": _normalize_tags(tags),
            "created_at": now,
            "updated_at": now,
            "source": "cockpit",
        }
        try:
            await ctx.interaction.save()
        except Exception as exc:
            return f"Error saving artifact: {exc}"
        return f"Artifact '{key}' stored ({len(artifacts[key]['data'])} chars)."

    async def _get(key: str) -> str:
        if not key or not str(key).strip():
            return "Error: 'key' is required."
        target = str(key).strip()

        # Walk current → past interactions newest-first; first match wins.
        all_artifacts = await _scan_conversation_artifacts(ctx)
        for k, entry, _is_current in all_artifacts:
            if k == target:
                return str(entry.get("data", ""))

        available = sorted({k for k, _, _ in all_artifacts})
        return (
            f"Artifact '{key}' not found. "
            f"Available in this conversation: {available if available else '(none)'}"
        )

    async def _update(key: str, data: Any, tags: Any = None) -> str:
        artifacts = _interaction_artifacts(ctx)
        if artifacts is None:
            return "Error: no interaction available."
        key = str(key).strip()
        entry = artifacts.get(key)
        if not entry:
            return (
                f"Error: artifact '{key}' does not exist. Use artifact_add to create."
            )
        entry["data"] = _normalize_data(data)
        if tags is not None:
            entry["tags"] = _normalize_tags(tags)
        entry["updated_at"] = _now_iso()
        try:
            await ctx.interaction.save()
        except Exception as exc:
            return f"Error saving artifact: {exc}"
        return f"Artifact '{key}' updated ({len(entry['data'])} chars)."

    async def _delete(key: str) -> str:
        artifacts = _interaction_artifacts(ctx)
        if artifacts is None:
            return "Error: no interaction available."
        key = str(key).strip()
        if key not in artifacts:
            return f"Artifact '{key}' not found."
        del artifacts[key]
        try:
            await ctx.interaction.save()
        except Exception as exc:
            return f"Error saving after delete: {exc}"
        return f"Artifact '{key}' deleted."

    async def _search(query: str = "", tag: str = "", limit: int = 10) -> str:
        all_artifacts = await _scan_conversation_artifacts(ctx)
        if not all_artifacts:
            return "No artifacts stored in this conversation."

        q = (query or "").strip().lower()
        t = (tag or "").strip().lower()

        # Dedupe by key — newest interaction wins (scan is newest-first).
        seen: Dict[str, Tuple[Dict[str, Any], bool]] = {}
        for key, entry, is_current in all_artifacts:
            if key not in seen:
                seen[key] = (entry, is_current)

        matches: List[Tuple[float, str, Dict[str, Any], bool]] = []
        for key, (entry, is_current) in seen.items():
            score = 0
            data = str(entry.get("data", "")).lower()
            tags = [str(x).lower() for x in (entry.get("tags") or [])]
            if q:
                if q in key.lower():
                    score += 3
                if q in data:
                    score += 1
                if any(q in tg for tg in tags):
                    score += 2
            if t:
                if t in tags:
                    score += 5
                else:
                    continue  # tag filter is exclusive
            if not q and not t:
                score = 1  # list-all when no filter given
            if score > 0:
                matches.append((score, key, entry, is_current))

        if not matches:
            filter_desc = []
            if q:
                filter_desc.append(f"query='{query}'")
            if t:
                filter_desc.append(f"tag='{tag}'")
            return f"No artifacts matched ({', '.join(filter_desc) or 'no filter'})."

        matches.sort(key=lambda x: x[0], reverse=True)
        matches = matches[: max(1, int(limit))]
        lines = [f"Found {len(matches)} artifact(s) across this conversation:"]
        for _, key, entry, is_current in matches:
            src = None if is_current else "prior"
            lines.append(_summarize(entry, key, source_iid=src))
        return "\n".join(lines)

    return [
        Tool(
            name="artifact_add",
            description=(
                "Store a new artifact (intermediate result, document, large tool output) "
                "under a unique key for later retrieval — including in follow-up turns. "
                "Writes are scoped to the current interaction; reads (artifact_get / "
                "artifact_search) span the whole conversation. Fails if the key already "
                "exists in the current interaction; use artifact_update to overwrite."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Unique name for this artifact within the interaction.",
                    },
                    "data": {
                        "type": "string",
                        "description": (
                            "The content to store. Pass as a string — JSON-stringify "
                            "objects/arrays yourself before calling."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for filtering.",
                    },
                },
                "required": ["key", "data"],
            },
            execute=_add,
        ),
        Tool(
            name="artifact_get",
            description=(
                "Retrieve a previously stored artifact's full contents by key. "
                "Searches the current interaction first, then walks back through "
                "prior interactions in the conversation; the most recent match wins."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The artifact key to retrieve.",
                    },
                },
                "required": ["key"],
            },
            execute=_get,
        ),
        Tool(
            name="artifact_update",
            description="Overwrite an existing artifact's data and optionally its tags.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "data": {
                        "type": "string",
                        "description": (
                            "Replacement content. Pass as a string — JSON-stringify "
                            "objects/arrays yourself before calling."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional replacement tags. Omit to keep existing tags.",
                    },
                },
                "required": ["key", "data"],
            },
            execute=_update,
        ),
        Tool(
            name="artifact_delete",
            description="Remove an artifact by key.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
            execute=_delete,
        ),
        Tool(
            name="artifact_search",
            description=(
                "Search artifacts across the whole conversation (current + prior "
                "interactions) by keyword and/or tag. Returns ranked summaries "
                "(key, tags, preview, source). Omit both filters to list everything. "
                "Use this first when answering follow-up questions to find anything you "
                "or a prior turn already saved."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to match against keys, content, and tags.",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Exact tag to filter by (exclusive).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10).",
                        "default": 10,
                    },
                },
            },
            execute=_search,
        ),
    ]
