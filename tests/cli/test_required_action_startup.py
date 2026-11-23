"""The HTTP listener must not start after a failed Action lifecycle."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock

import pytest

from jvagent.cli.server_config import pre_startup_bootstrap
from jvagent.core.errors import ConfigError


@pytest.mark.asyncio
async def test_failed_action_initialization_aborts_before_admin_and_mode_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = SimpleNamespace(config=SimpleNamespace(scheduler_enabled=False))

    @contextmanager
    def context(_server: object) -> Iterator[None]:
        yield

    monkeypatch.setattr("jvagent.core.index_bootstrap.run_index_migration", AsyncMock())
    monkeypatch.setattr(
        "jvagent.cli.server_config.resolve_bootstrap_update_mode",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "jvagent.cli.server_config.bootstrap_application_graph", AsyncMock()
    )
    monkeypatch.setattr(
        "jvagent.cli.server_config.app_has_task_monitor", lambda *_: False
    )
    monkeypatch.setattr(
        "jvagent.core.startup.run_app_startup", AsyncMock(return_value=False)
    )
    monkeypatch.setattr("jvspatial.api.context.ServerContext", context)
    admin, reset = AsyncMock(), AsyncMock()
    monkeypatch.setattr("jvagent.cli.server_config.ensure_admin_user", admin)
    monkeypatch.setattr(
        "jvagent.cli.server_config.reset_app_update_mode_after_successful_bootstrap",
        reset,
    )
    with pytest.raises(ConfigError, match="initialization failed"):
        await pre_startup_bootstrap(server, app_root="/unused")
    admin.assert_not_awaited()
    reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_command_refuses_failed_action_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jvagent.cli.server as module

    for name in (
        "load_app_env",
        "_set_db_env_from_config",
        "create_server_from_config",
    ):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
    monkeypatch.setattr("jvagent.core.index_bootstrap.run_index_migration", AsyncMock())
    monkeypatch.setattr(
        module, "resolve_bootstrap_update_mode", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(module, "bootstrap_application_graph", AsyncMock())
    monkeypatch.setattr(
        "jvagent.core.startup.run_app_startup", AsyncMock(return_value=False)
    )
    admin, reset = AsyncMock(), AsyncMock()
    monkeypatch.setattr(module, "ensure_admin_user", admin)
    monkeypatch.setattr(
        module, "reset_app_update_mode_after_successful_bootstrap", reset
    )
    with pytest.raises(ConfigError, match="initialization failed"):
        await module.bootstrap_only(app_root="/unused")
    admin.assert_not_awaited()
    reset.assert_not_awaited()
