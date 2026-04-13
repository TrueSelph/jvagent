"""Pure helpers for long-memory PageIndex collection resolution.

Lives under ``jvagent.memory`` so tests can import without loading PageIndex.
"""

from typing import Any, Dict, Optional


def resolve_long_memory_collection(
    agent_id: Optional[str],
    collection_attr: Optional[str],
    config: Optional[Dict[str, Any]],
) -> str:
    """Build PageIndex collection name ``{agent_id}_{suffix}`` for long-memory docs."""
    aid = (agent_id or "").strip()
    cfg = config or {}
    suffix = cfg.get("collection") or cfg.get("collection_name")
    if not suffix:
        coll = collection_attr
        suffix = (coll or "").strip() if coll is not None else ""
    if not suffix:
        suffix = "LongTermMemory"
    if aid:
        return f"{aid}_{suffix}"
    return suffix or "default"
