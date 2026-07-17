"""Enforce ``App.log_retention_days`` against the logs database.

``0`` disables purge (operators who want unbounded retention must set it
explicitly). Default on ``App`` is 60 days.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def purge_logs_past_retention(
    *,
    retention_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Delete DBLog rows older than ``retention_days``.

    When ``retention_days`` is omitted, reads ``App.log_retention_days``.
    Returns ``{"deleted": N, "skipped": reason?}``.
    """
    days = retention_days
    if days is None:
        from jvagent.core.app import App

        app = await App.get()
        days = int(getattr(app, "log_retention_days", 60) or 0)

    if days <= 0:
        return {"deleted": 0, "skipped": "retention_disabled"}

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    cutoff = now_dt - timedelta(days=int(days))

    try:
        from jvspatial.logging.service import get_logging_service

        service = get_logging_service(database_name="logs")
        result = await service.purge_error_logs(end_time=cutoff)
        deleted = int(result.get("deleted") or 0)
        if deleted:
            logger.info(
                "log retention: purged %s rows older than %s days (cutoff=%s)",
                deleted,
                days,
                cutoff.isoformat(),
            )
        out: Dict[str, Any] = {"deleted": deleted, "cutoff": cutoff.isoformat()}
        if result.get("error"):
            out["error"] = result["error"]
        return out
    except Exception as e:
        logger.warning("log retention purge failed: %s", e)
        return {"deleted": 0, "error": str(e)}


__all__ = ["purge_logs_past_retention"]
