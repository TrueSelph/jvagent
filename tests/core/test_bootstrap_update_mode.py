"""Tests for persisted App.update_mode and bootstrap resolution."""

import pytest
from jvspatial.core import Root

from jvagent.core.app import App, set_app_update_mode
from jvagent.core.app_loader import AppLoader
from jvagent.core.bootstrap_update_mode import (
    normalize_stored_update_mode,
    reset_app_update_mode_after_successful_bootstrap,
    resolve_bootstrap_update_mode,
    stored_update_mode_to_bootstrap_arg,
    validate_admin_update_mode,
)


class TestBootstrapUpdateModePure:
    def test_normalize_defaults(self):
        assert normalize_stored_update_mode(None) == "run"
        assert normalize_stored_update_mode("") == "run"
        assert normalize_stored_update_mode("  ") == "run"
        assert normalize_stored_update_mode("MERGE") == "merge"

    def test_normalize_invalid_warns_to_run(self, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        assert normalize_stored_update_mode("nope") == "run"

    def test_stored_to_bootstrap_arg(self):
        assert stored_update_mode_to_bootstrap_arg("run") is None
        assert stored_update_mode_to_bootstrap_arg("merge") == "merge"
        assert stored_update_mode_to_bootstrap_arg("source") == "source"

    def test_validate_admin(self):
        assert validate_admin_update_mode("merge") == "merge"
        with pytest.raises(ValueError):
            validate_admin_update_mode("")
        with pytest.raises(ValueError):
            validate_admin_update_mode("invalid")


@pytest.mark.asyncio
class TestBootstrapUpdateModeIntegration:
    async def test_resolve_none_when_no_app(self, test_db):
        await Root.get()
        App.clear_cache()
        assert await resolve_bootstrap_update_mode(None) is None

    async def test_resolve_from_graph_merge(self, temp_dir, test_db):
        await Root.get()
        (temp_dir / "app.yaml").write_text(
            """app: test_app
version: 1.0.0
author: Test
agents: []
"""
        )
        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode=None)
        app = await App.get()
        assert app is not None
        await set_app_update_mode(app, "merge")
        App.clear_cache()
        assert await resolve_bootstrap_update_mode(None) == "merge"

    async def test_cli_overrides_graph(self, temp_dir, test_db):
        await Root.get()
        (temp_dir / "app.yaml").write_text(
            """app: test_app
version: 1.0.0
author: Test
agents: []
"""
        )
        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode=None)
        app = await App.get()
        await set_app_update_mode(app, "merge")
        App.clear_cache()
        assert await resolve_bootstrap_update_mode("source") == "source"

    async def test_reset_to_run(self, temp_dir, test_db):
        await Root.get()
        (temp_dir / "app.yaml").write_text(
            """app: test_app
version: 1.0.0
author: Test
agents: []
"""
        )
        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode=None)
        app = await App.get()
        await set_app_update_mode(app, "source")
        await reset_app_update_mode_after_successful_bootstrap()
        App.clear_cache()
        app2 = await App.get()
        assert app2.update_mode == "run"
