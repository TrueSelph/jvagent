"""Tests for startup coordinator behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import jvagent.core.startup as startup


@pytest.mark.asyncio
async def test_run_app_startup_returns_false_when_actions_fail():
    startup._startup_completed = False
    app = SimpleNamespace(
        initialize_actions=AsyncMock(return_value={"A": True, "B": False})
    )

    with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
        result = await startup.run_app_startup()

    assert result is False
    assert startup._startup_completed is False


@pytest.mark.asyncio
async def test_failed_startup_backs_off_instead_of_retrying_every_call():
    """A failed startup must not re-run full action init on every request.

    Regression: only success set the completion flag, so a persistently
    failing action re-triggered ``initialize_actions`` per call — a retry
    storm on the request path.
    """
    startup._startup_completed = False
    startup._startup_last_failure = 0.0
    app = SimpleNamespace(initialize_actions=AsyncMock(return_value={"A": False}))

    with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
        assert await startup.run_app_startup() is False
        assert await startup.run_app_startup() is False  # inside backoff window

    assert app.initialize_actions.await_count == 1
    startup._startup_last_failure = 0.0


@pytest.mark.asyncio
async def test_failed_startup_retries_after_backoff_window(monkeypatch):
    """After the backoff window elapses the next call attempts init again."""
    startup._startup_completed = False
    startup._startup_last_failure = 0.0
    app = SimpleNamespace(initialize_actions=AsyncMock(return_value={"A": False}))

    now = {"t": 1000.0}
    monkeypatch.setattr(startup.time, "monotonic", lambda: now["t"])

    with patch("jvagent.core.app.App.get", AsyncMock(return_value=app)):
        assert await startup.run_app_startup() is False
        now["t"] += startup._STARTUP_RETRY_SECONDS + 1
        assert await startup.run_app_startup() is False

    assert app.initialize_actions.await_count == 2
    startup._startup_last_failure = 0.0
