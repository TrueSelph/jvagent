"""Scheduling compares actual UTC instants, including the server-local clock."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.core.app import app_now_aware_utc


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="Requires process timezone control"
)
@pytest.mark.parametrize("configured", [None, "America/Guyana", "UTC"])
async def test_naive_app_clock_is_localized_before_utc(
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
) -> None:
    try:
        with monkeypatch.context() as clock_env:
            clock_env.setenv("TZ", "America/Guyana")
            time.tzset()
            app = SimpleNamespace(
                timezone=configured,
                now=AsyncMock(return_value=datetime(2026, 11, 5, 12)),
            )
            result = await app_now_aware_utc(app)
            assert result == datetime(
                2026, 11, 5, 12 if configured == "UTC" else 16, tzinfo=timezone.utc
            )
            assert result.tzinfo == timezone.utc
    finally:
        time.tzset()
