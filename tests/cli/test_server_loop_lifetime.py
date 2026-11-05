"""Bootstrap-owned async resources must survive into HTTP serving."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jvagent.cli import server as cli_server


@pytest.mark.parametrize("serverless", [False, True])
def test_bootstrap_scheduler_dispatches_on_serving_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, serverless: bool
) -> None:
    pytest.importorskip("schedule")
    from jvspatial.api.integrations.scheduler.models import (
        ScheduleConfig,
        ScheduledTask,
        SchedulerConfig,
    )
    from jvspatial.api.integrations.scheduler.scheduler import SchedulerService

    from jvagent import stress_seed_graph

    seed = stress_seed_graph.StressSeedConfig(1, 1)
    scheduler = SchedulerService(SchedulerConfig(interval=0.01))
    state: dict = {}

    async def probe() -> None:
        state["dispatch_loop"] = asyncio.get_running_loop()
        state["delivered"].set()

    async def bootstrap(server: object, **kwargs: object) -> bool:
        state["bootstrap_loop"] = asyncio.get_running_loop()
        state["delivered"] = asyncio.Event()
        await scheduler.register_task(
            ScheduledTask(
                task_id="cli-loop-probe",
                task_type="async_function",
                function_ref=probe,
                schedule=ScheduleConfig(schedule_spec="every 1 seconds"),
            )
        )
        scheduler.start(interval=0.01)
        return True

    async def seed_graph(config: stress_seed_graph.StressSeedConfig) -> None:
        assert config is seed
        assert asyncio.get_running_loop() is state["bootstrap_loop"]
        state["seeded"] = True

    async def run_async(**kwargs: object) -> None:
        assert asyncio.get_running_loop() is state["bootstrap_loop"]
        expected = {
            "reload": False,
            "timeout_graceful_shutdown": 30,
        }
        if serverless:
            expected["workers"] = 1
        assert kwargs == expected
        assert state["seeded"] is True
        await asyncio.wait_for(state["delivered"].wait(), timeout=5)
        assert state["dispatch_loop"] is state["bootstrap_loop"]

    # The synchronous API models Uvicorn creating its own loop. Restoring the
    # old CLI implementation must fail the loop-identity assertion above.
    server = SimpleNamespace(
        lifecycle_manager=SimpleNamespace(add_startup_hook=MagicMock()),
        run_async=run_async,
        run=lambda **kwargs: asyncio.run(run_async(**kwargs)),
    )
    monkeypatch.setattr(cli_server, "create_server_from_config", lambda **kw: server)
    monkeypatch.setattr(cli_server, "pre_startup_bootstrap", bootstrap)
    monkeypatch.setattr(cli_server, "is_serverless_mode", lambda: serverless)
    monkeypatch.setattr(stress_seed_graph, "execute_stress_seed_graph", seed_graph)
    try:
        cli_server.run_server(app_root=str(tmp_path), debug=True, stress_seed=seed)
    finally:
        scheduler.stop()
        scheduler.unregister_task("cli-loop-probe")


def test_failed_bootstrap_does_not_serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = SimpleNamespace(run_async=MagicMock(), run=MagicMock())

    async def bootstrap(server: object, **kwargs: object) -> bool:
        raise RuntimeError("required Action startup failed")

    monkeypatch.setattr(cli_server, "create_server_from_config", lambda **kw: server)
    monkeypatch.setattr(cli_server, "pre_startup_bootstrap", bootstrap)
    with pytest.raises(RuntimeError, match="required Action startup failed"):
        cli_server.run_server(app_root=str(tmp_path))
    server.run_async.assert_not_called()
    server.run.assert_not_called()
