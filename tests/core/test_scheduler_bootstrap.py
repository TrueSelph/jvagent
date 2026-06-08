"""Tests for jvagent scheduler bootstrap helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jvagent.core.scheduler_bootstrap import app_has_task_monitor


def test_app_has_task_monitor_detects_action(tmp_path: Path):
    agents = tmp_path / "agents" / "jvagent" / "demo_agent"
    agents.mkdir(parents=True)
    (agents / "agent.yaml").write_text(
        "actions:\n  - action: jvagent/task_monitor\n",
        encoding="utf-8",
    )
    assert app_has_task_monitor(str(tmp_path)) is True


def test_app_has_task_monitor_false_when_missing(tmp_path: Path):
    assert app_has_task_monitor(str(tmp_path)) is False


@pytest.mark.asyncio
async def test_ensure_scheduler_for_server_attaches_service():
    from jvagent.core.scheduler_bootstrap import ensure_scheduler_for_server

    pytest.importorskip("schedule")
    server = MagicMock()
    server.config.scheduler_interval = 1
    server._graph_context = None
    server.scheduler_service = None

    svc = await ensure_scheduler_for_server(server, start=False)
    assert svc is not None
    assert server.scheduler_service is svc
