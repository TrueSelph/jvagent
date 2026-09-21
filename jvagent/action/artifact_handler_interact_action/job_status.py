"""Shared ingest-job status writer for conversation vault + pending jobs."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

VAULT_CTX_KEY = "artifact_handler"
PENDING_JOBS_KEY = "pending_ingest_jobs"
ACTIVE_DOC_KEY = "active_doc_name"

PROCESSING_STATUSES = frozenset({"queued", "processing", "pending", "submitted"})
FAILED_JOB_STATUSES = frozenset(
    {"failed", "error", "cancelled", "canceled", "webhook_failed"}
)
READY_STATUSES = frozenset({"ready", "ingested"})

_VAULT_META_KEYS = frozenset({PENDING_JOBS_KEY, ACTIVE_DOC_KEY})


def _now_ts() -> int:
    return int(time.time())


async def apply_ingest_job_status(
    conversation: Any,
    job_id: str,
    status: str,
    *,
    doc_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Set ``queued`` / ``ready`` / ``failed`` on pending jobs and vault entries.

    Updates ``pending_ingest_jobs[job_id]`` and any vault list entry that
    matches ``job_id`` (or ``doc_name`` when the entry has no job_id).
    Returns True when the conversation context was persisted.
    """
    jid = str(job_id or "").strip()
    new_status = str(status or "").strip().lower()
    if conversation is None or not jid or not new_status:
        return False

    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        ctx = {}
    vault = ctx.get(VAULT_CTX_KEY)
    if not isinstance(vault, dict):
        vault = {}
    else:
        vault = dict(vault)

    pending_raw = vault.get(PENDING_JOBS_KEY)
    pending: Dict[str, Dict[str, Any]] = {}
    if isinstance(pending_raw, dict):
        for key, value in pending_raw.items():
            if isinstance(value, dict) and key:
                pending[str(key)] = dict(value)

    entry = dict(pending.get(jid) or {})
    entry["status"] = new_status
    name = str(doc_name or "").strip()
    if name:
        entry["doc_name"] = name
    if extra:
        for key, value in extra.items():
            if value is not None:
                entry[key] = value
    if new_status in READY_STATUSES:
        entry.setdefault("ready_at", _now_ts())
    elif new_status in FAILED_JOB_STATUSES:
        entry.setdefault("failed_at", _now_ts())
    pending[jid] = entry
    vault[PENDING_JOBS_KEY] = pending

    match_name = name or str(entry.get("doc_name") or "").strip()
    for key, value in list(vault.items()):
        if key in _VAULT_META_KEYS or not isinstance(value, list):
            continue
        updated: list = []
        for item in value:
            if not isinstance(item, dict):
                updated.append(item)
                continue
            row = dict(item)
            row_job = str(row.get("job_id") or "").strip()
            row_name = str(row.get("doc_name") or "").strip()
            matched = row_job == jid or (
                not row_job and match_name and row_name == match_name
            )
            if matched:
                row["status"] = new_status
                row["job_id"] = jid
                if match_name:
                    row["doc_name"] = match_name
                if new_status in READY_STATUSES:
                    row.setdefault("ready_at", entry.get("ready_at"))
                elif new_status in FAILED_JOB_STATUSES:
                    row.setdefault("failed_at", entry.get("failed_at"))
            updated.append(row)
        vault[key] = updated

    if new_status in READY_STATUSES and match_name:
        vault[ACTIVE_DOC_KEY] = match_name

    try:
        await conversation.update_context({VAULT_CTX_KEY: vault})
    except Exception:
        logger.warning(
            "artifact_handler apply_ingest_job_status: context update failed "
            "job_id=%s status=%s",
            jid,
            new_status,
            exc_info=True,
        )
        return False
    return True
