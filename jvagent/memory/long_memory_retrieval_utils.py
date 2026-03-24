"""Pure helpers for long-memory PageIndex collection resolution and keyword overlap.

Lives under ``jvagent.memory`` so tests can import without loading PageIndex.
"""

from typing import Any, Dict, List, Optional

_KEYWORD_OVERLAP_MIN_LEN = 2


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


def utterance_overlaps_category_keywords(
    categories: List[Any],
    utterance: str,
    interpretation: str,
) -> bool:
    """True if any non-empty category keyword appears as a substring of utterance/interpretation."""
    blob = " ".join(
        x for x in ((utterance or "").lower(), (interpretation or "").lower()) if x
    )
    if not blob.strip():
        return False
    for c in categories:
        if c.is_empty():
            continue
        for kw in getattr(c, "keywords", None) or []:
            needle = str(kw).strip().lower()
            if len(needle) >= _KEYWORD_OVERLAP_MIN_LEN and needle in blob:
                return True
    return False
