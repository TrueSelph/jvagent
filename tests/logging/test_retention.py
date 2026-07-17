"""Tests for App.log_retention_days enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.logging.retention import purge_logs_past_retention

pytestmark = pytest.mark.asyncio


async def test_purge_disabled_when_zero():
    result = await purge_logs_past_retention(retention_days=0)
    assert result["deleted"] == 0
    assert result["skipped"] == "retention_disabled"


async def test_purge_calls_logging_service(monkeypatch):
    service = MagicMock()
    service.purge_error_logs = AsyncMock(return_value={"deleted": 3})
    monkeypatch.setattr(
        "jvspatial.logging.service.get_logging_service",
        lambda database_name="logs": service,
    )
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    result = await purge_logs_past_retention(retention_days=60, now=now)
    assert result["deleted"] == 3
    service.purge_error_logs.assert_awaited_once()
    kwargs = service.purge_error_logs.await_args.kwargs
    assert "end_time" in kwargs
    assert kwargs["end_time"].year == 2026
