"""Backend-agnostic lexical index for PageIndex vectorless retrieval.

Stores an inverted index (term -> posting lists) and collection statistics as
records in the existing PageIndex database.  Uses deterministic record IDs so
that query-time lookups are O(|query_terms|) ``get()`` calls instead of
collection-wide scans.

Write-path (ingestion):
    index_node()          -- index a single DocumentNode
    remove_node()         -- remove a single node from the index
    remove_document_nodes() -- batch-remove nodes (pre-deletion cleanup)
    remove_collection()   -- wipe all index data for a collection
    reindex_nodes()       -- (re)build index from existing DocumentNode list

Read-path (search):
    search()              -- BM25-ranked candidate retrieval
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional, Set

from jvspatial.db import get_database_manager

from .config import PAGEINDEX_DB_NAME
from .tokenizer import tokenize, tokenize_fields

logger = logging.getLogger(__name__)

_POSTINGS_COLLECTION = "lexical_postings"
_STATS_COLLECTION = "lexical_stats"
_MAX_POSTINGS_PER_TERM = 2000


def _posting_id(collection_name: str, term: str) -> str:
    return f"lp.{collection_name}.{term}"


def _collection_stats_id(collection_name: str) -> str:
    return f"cs.{collection_name}"


def _node_meta_id(collection_name: str, node_id: str) -> str:
    h = hashlib.sha256(node_id.encode()).hexdigest()[:16]
    return f"nm.{collection_name}.{h}"


def _get_db():
    manager = get_database_manager()
    return manager.get_database(PAGEINDEX_DB_NAME)


# ---------------------------------------------------------------------------
# Write-path
# ---------------------------------------------------------------------------


async def index_node(
    node_id: str,
    doc_name: str,
    collection_name: str,
    title: str = "",
    text: str = "",
    summary: str = "",
    prefix_summary: str = "",
) -> None:
    """Index a single DocumentNode into the lexical index."""
    db = _get_db()

    tf_map, total_len = tokenize_fields(title, text, summary, prefix_summary)
    if not tf_map:
        return

    terms = list(tf_map.keys())

    # Persist node metadata (term list for deletion cleanup)
    nm_id = _node_meta_id(collection_name, node_id)
    await db.save(
        _STATS_COLLECTION,
        {
            "id": nm_id,
            "node_id": node_id,
            "doc_name": doc_name,
            "collection_name": collection_name,
            "terms": terms,
        },
    )

    # Update collection-level stats (total_nodes, sum_doc_len for avg)
    cs_id = _collection_stats_id(collection_name)
    stats = await db.get(_STATS_COLLECTION, cs_id)
    if stats:
        stats["total_nodes"] = stats.get("total_nodes", 0) + 1
        stats["sum_doc_len"] = stats.get("sum_doc_len", 0.0) + total_len
    else:
        stats = {
            "id": cs_id,
            "collection_name": collection_name,
            "total_nodes": 1,
            "sum_doc_len": float(total_len),
        }
    await db.save(_STATS_COLLECTION, stats)

    # Append to posting lists
    for term, tf in tf_map.items():
        pid = _posting_id(collection_name, term)
        record = await db.get(_POSTINGS_COLLECTION, pid)
        new_posting = {
            "node_id": node_id,
            "doc_name": doc_name,
            "tf": tf,
            "dl": total_len,
        }

        if record:
            postings = record.get("postings", [])
            postings.append(new_posting)
            if len(postings) > _MAX_POSTINGS_PER_TERM:
                logger.warning(
                    "Lexical index: posting list for term '%s' in collection "
                    "'%s' exceeds cap (%d); truncating oldest entries. "
                    "Recall may degrade for this high-frequency term — "
                    "consider raising _MAX_POSTINGS_PER_TERM or sharding.",
                    term,
                    collection_name,
                    _MAX_POSTINGS_PER_TERM,
                )
                postings = postings[-_MAX_POSTINGS_PER_TERM:]
            record["postings"] = postings
            await db.save(_POSTINGS_COLLECTION, record)
        else:
            await db.save(
                _POSTINGS_COLLECTION,
                {
                    "id": pid,
                    "term": term,
                    "collection_name": collection_name,
                    "postings": [new_posting],
                },
            )


async def remove_node(node_id: str, collection_name: str) -> None:
    """Remove a single node from the lexical index."""
    db = _get_db()

    nm_id = _node_meta_id(collection_name, node_id)
    meta = await db.get(_STATS_COLLECTION, nm_id)
    if not meta:
        return

    terms = meta.get("terms", [])

    # Scrub postings; capture this node's document length on the way through
    # so collection stats can be decremented accurately. (Reading dl AFTER the
    # filter would always return 0 because the posting is already gone.)
    removed_len = 0.0
    for term in terms:
        pid = _posting_id(collection_name, term)
        record = await db.get(_POSTINGS_COLLECTION, pid)
        if not record:
            continue
        kept: List[Dict[str, Any]] = []
        for p in record.get("postings", []):
            if p["node_id"] == node_id:
                if not removed_len:
                    removed_len = p.get("dl", 0) or 0.0
            else:
                kept.append(p)
        if kept:
            record["postings"] = kept
            await db.save(_POSTINGS_COLLECTION, record)
        else:
            await db.delete(_POSTINGS_COLLECTION, pid)

    cs_id = _collection_stats_id(collection_name)
    stats = await db.get(_STATS_COLLECTION, cs_id)
    if stats:
        stats["total_nodes"] = max(0, stats.get("total_nodes", 1) - 1)
        stats["sum_doc_len"] = max(0.0, stats.get("sum_doc_len", 0.0) - removed_len)
        await db.save(_STATS_COLLECTION, stats)

    await db.delete(_STATS_COLLECTION, nm_id)


async def remove_document_nodes(
    node_ids: List[str],
    collection_name: str,
) -> None:
    """Batch-remove nodes from the lexical index (call before graph deletion)."""
    for nid in node_ids:
        try:
            await remove_node(nid, collection_name)
        except Exception:
            logger.debug(f"Lexical index: failed to remove node {nid}", exc_info=True)


async def remove_collection(collection_name: str) -> None:
    """Remove all lexical index data for a collection."""
    db = _get_db()

    for coll in (_POSTINGS_COLLECTION, _STATS_COLLECTION):
        try:
            records = await db.find(coll, {})
        except Exception:
            continue
        for record in records:
            if record.get("collection_name") == collection_name:
                try:
                    await db.delete(coll, record["id"])
                except Exception:
                    pass


async def reindex_nodes(
    nodes: List[Dict[str, Any]],
    collection_name: str,
) -> int:
    """(Re)build lexical index entries for a batch of node dicts.

    Each dict must contain ``id`` (jvspatial node ID), ``doc_name``,
    ``title``, ``text``, and optionally ``summary`` / ``prefix_summary``.
    Returns the number of nodes indexed.
    """
    count = 0
    for n in nodes:
        nid = n.get("id", "")
        if not nid:
            continue
        try:
            await index_node(
                node_id=nid,
                doc_name=n.get("doc_name", ""),
                collection_name=collection_name,
                title=n.get("title", ""),
                text=n.get("text", ""),
                summary=n.get("summary") or "",
                prefix_summary=n.get("prefix_summary") or "",
            )
            count += 1
        except Exception:
            logger.debug(f"Lexical index: failed to index node {nid}", exc_info=True)
    return count


# ---------------------------------------------------------------------------
# Read-path
# ---------------------------------------------------------------------------


async def search(
    query: str,
    collection_name: str,
    doc_name: Optional[str] = None,
    allowed_doc_names: Optional[List[str]] = None,
    candidate_k: int = 200,
) -> List[Dict[str, Any]]:
    """BM25-ranked candidate retrieval from the lexical index.

    Args:
        query: Raw search query text.
        collection_name: Collection scope.
        doc_name: If set, restrict to a single document.
        allowed_doc_names: If set, restrict to these documents (from metadata
            filter resolution).
        candidate_k: Maximum candidates to return.

    Returns:
        List of ``{node_id, doc_name, score}`` dicts sorted by score
        descending.  Empty list when the lexical index has no data for this
        collection (triggers graceful fallback to original retrieval).
    """
    from .ranking import bm25_score

    db = _get_db()

    query_terms = tokenize(query)
    if not query_terms:
        return []

    unique_terms = list(dict.fromkeys(query_terms))

    cs_id = _collection_stats_id(collection_name)
    stats = await db.get(_STATS_COLLECTION, cs_id)
    if not stats:
        return []

    total_nodes = stats.get("total_nodes", 0)
    sum_doc_len = stats.get("sum_doc_len", 0.0)
    avg_doc_len = sum_doc_len / total_nodes if total_nodes > 0 else 1.0

    allowed_set: Optional[Set[str]] = None
    if doc_name:
        allowed_set = {doc_name}
    elif allowed_doc_names is not None:
        allowed_set = set(allowed_doc_names)

    postings_map: Dict[str, List[Dict[str, Any]]] = {}
    # Capture full-corpus df per term BEFORE applying allowed_set filter so
    # BM25 IDF stays stable when the caller scopes by doc_name / metadata.
    term_df_map: Dict[str, int] = {}

    for term in unique_terms:
        pid = _posting_id(collection_name, term)
        record = await db.get(_POSTINGS_COLLECTION, pid)
        if not record:
            continue
        postings = record.get("postings", [])
        term_df_map[term] = len(postings)
        if allowed_set is not None:
            postings = [p for p in postings if p["doc_name"] in allowed_set]
        if postings:
            postings_map[term] = postings

    if not postings_map:
        return []

    ranked = bm25_score(
        query_terms=unique_terms,
        postings_map=postings_map,
        total_nodes=total_nodes,
        avg_doc_len=avg_doc_len,
        term_df_map=term_df_map,
    )

    return ranked[:candidate_k]
