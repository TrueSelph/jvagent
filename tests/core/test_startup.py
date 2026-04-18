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
