"""Startup bootstrap hooks (log retention, etc.)."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pre_startup_bootstrap_purges_logs():
    from jvagent.cli.server_config import pre_startup_bootstrap

    server = MagicMock()
    purge = AsyncMock(return_value={"deleted": 0})

    @contextmanager
    def fake_server_context(_server):
        yield

    with (
        patch("jvagent.core.index_bootstrap.run_index_migration", new=AsyncMock()),
        patch(
            "jvagent.core.bootstrap_update_mode.resolve_bootstrap_update_mode",
            new=AsyncMock(return_value=None),
        ),
        patch("jvagent.cli.server_config.bootstrap_application_graph", new=AsyncMock()),
        patch("jvagent.core.startup.run_app_startup", new=AsyncMock()),
        patch(
            "jvagent.cli.server_config.ensure_admin_user",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "jvagent.core.bootstrap_update_mode.reset_app_update_mode_after_successful_bootstrap",
            new=AsyncMock(),
        ),
        patch("jvspatial.api.context.ServerContext", side_effect=fake_server_context),
        patch("jvagent.logging.retention.purge_logs_past_retention", new=purge),
    ):
        await pre_startup_bootstrap(server, app_root="/tmp/app")

    purge.assert_awaited_once()
