"""Scratch collection helpers for graph repair.

The repair engine previously stored bulk working sets (all node ids, BFS visited
sets, edge accumulators) directly inside the ``RepairState`` cursor dict.  For
large graphs this JSON payload easily exceeds MongoDB's 16 MB document limit and
causes silent stalls.

This module provides helpers that store those working sets in a dedicated
``repair_scratch`` collection keyed by ``run_id``.  Each document is small
(one item per document) and the collection carries a TTL index so stale scratch
data auto-expires within 24 hours.

Collection schema::

    {
      "id":         "<run_id>:<kind>:<key>",   # PK - used for upserts
      "_id":        "<run_id>:<kind>:<key>",
      "run_id":     str,
      "kind":       str,     # "node_id" | "bfs_seen" | "node_edge" | "valid_edge" | "edge_pair"
      "key":        str,     # e.g. node_id, edge_id, "source\\ntarget"
      "value":      str,     # optional secondary value (e.g. edge_id for node_edge rows)
      "created_at": float,   # Unix timestamp for TTL
    }
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCRATCH_COLLECTION = "repair_scratch"
SCRATCH_TTL_SECONDS = 86400  # 24 hours


def _doc_id(run_id: str, kind: str, key: str) -> str:
    return f"{run_id}:{kind}:{key}"


async def ensure_scratch_indexes(db: Any) -> None:
    """Create indexes on the scratch collection if the backend supports it.

    Called once when a new repair run starts.  Idempotent and swallows errors
    on backends that do not support index creation (JSON, SQLite).

    Respects ``JVSPATIAL_AUTO_CREATE_INDEXES`` / serverless mode so that
    Lambda cold-starts do not incur unnecessary index-creation round-trips.
    """
    if not hasattr(db, "create_index"):
        return

    # Mirror the same gate used by context.ensure_indexes so serverless
    # deployments (where JVSPATIAL_AUTO_CREATE_INDEXES defaults to False)
    # are not slowed by index DDL on every cold start.
    try:
        from jvspatial.env import env, parse_bool_basic
        from jvspatial.runtime.serverless import is_serverless_mode

        auto_create = env(
            "JVSPATIAL_AUTO_CREATE_INDEXES",
            default=not is_serverless_mode(),
            parse=parse_bool_basic,
        )
        if not auto_create:
            return
    except Exception:
        pass  # If env helpers aren't available, proceed with index creation.

    try:
        # Compound index for paged sweeps by run_id + kind
        await db.create_index(
            SCRATCH_COLLECTION,
            [("run_id", 1), ("kind", 1), ("key", 1)],
            name="repair_scratch_run_kind_key",
            unique=True,
            background=True,
        )
        # TTL index for auto-expiry (MongoDB only)
        await db.create_index(
            SCRATCH_COLLECTION,
            [("created_at", 1)],
            name="repair_scratch_ttl",
            expireAfterSeconds=SCRATCH_TTL_SECONDS,
            background=True,
        )
    except Exception:
        logger.debug("ensure_scratch_indexes: index creation skipped", exc_info=True)


async def scratch_upsert_bulk(
    db: Any,
    run_id: str,
    kind: str,
    items: List[Tuple[str, str]],
) -> None:
    """Upsert many (key, value) rows of ``kind`` into the scratch collection.

    Uses ``db.save`` with ``_id``-based upserts so each row is idempotent.
    On backends with ``batch_write`` (DynamoDB) this can be overridden, but the
    sequential ``save`` loop is safe on all backends.
    """
    ts = time.time()
    for key, value in items:
        doc_id = _doc_id(run_id, kind, key)
        doc = {
            "id": doc_id,
            "_id": doc_id,
            "run_id": run_id,
            "kind": kind,
            "key": key,
            "value": value,
            "created_at": ts,
        }
        try:
            await db.save(SCRATCH_COLLECTION, doc)
        except Exception:
            logger.debug(
                "scratch_upsert_bulk: save failed for %s/%s", kind, key, exc_info=True
            )


async def scratch_contains(db: Any, run_id: str, kind: str, key: str) -> bool:
    """Return True if a scratch row for (run_id, kind, key) exists."""
    doc_id = _doc_id(run_id, kind, key)
    try:
        row = await db.get(SCRATCH_COLLECTION, doc_id)
        return row is not None
    except Exception:
        return False


async def scratch_page(
    db: Any,
    run_id: str,
    kind: str,
    after_key: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Return a page of scratch rows for (run_id, kind) ordered by key.

    ``after_key`` is the last key seen (exclusive lower bound for pagination).
    """
    prefix_key_start = f"{run_id}:{kind}:"
    try:
        if after_key:
            doc_id_after = _doc_id(run_id, kind, after_key)
            rows = await db.find(
                SCRATCH_COLLECTION,
                {"id": {"$gt": doc_id_after, "$lt": f"{run_id}:{kind}:~"}},
                limit=limit,
                sort=[("id", 1)],
            )
        else:
            rows = await db.find(
                SCRATCH_COLLECTION,
                {"id": {"$gte": prefix_key_start, "$lt": f"{run_id}:{kind}:~"}},
                limit=limit,
                sort=[("id", 1)],
            )
        return rows or []
    except Exception:
        logger.debug("scratch_page: query failed", exc_info=True)
        return []


async def scratch_count(db: Any, run_id: str, kind: str) -> int:
    """Count scratch rows for (run_id, kind)."""
    try:
        prefix_start = f"{run_id}:{kind}:"
        rows = await db.find(
            SCRATCH_COLLECTION,
            {"id": {"$gte": prefix_start, "$lt": f"{run_id}:{kind}:~"}},
            limit=0,
        )
        return len(rows) if rows else 0
    except Exception:
        return 0


async def scratch_drop_run(db: Any, run_id: str) -> None:
    """Delete all scratch rows for a completed or aborted run."""
    try:
        rows = await db.find(
            SCRATCH_COLLECTION,
            {"run_id": run_id},
        )
        for row in rows or []:
            row_id = row.get("id") or row.get("_id")
            if row_id:
                try:
                    await db.delete(SCRATCH_COLLECTION, row_id)
                except Exception:
                    pass
    except Exception:
        logger.debug("scratch_drop_run: failed for %s", run_id, exc_info=True)
