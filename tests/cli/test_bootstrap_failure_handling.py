"""Declarative bootstrap failure must fail loudly, not fabricate a default App
(AUDIT-cli M22)."""

from __future__ import annotations

import pytest

from jvagent.cli.bootstrap import bootstrap_application_graph
from jvagent.core.app import App

pytestmark = pytest.mark.asyncio


async def test_declarative_failure_raises_not_masks(tmp_path, monkeypatch, test_db):
    App.clear_cache()
    (tmp_path / "app.yaml").write_text("app: demo\n")

    class _FailLoader:
        def __init__(self, root):
            pass

        async def bootstrap_application(self, update_mode=None):
            return None  # simulate a declarative bootstrap failure

    monkeypatch.setattr("jvagent.cli.bootstrap.AppLoader", _FailLoader)

    with pytest.raises(RuntimeError):
        await bootstrap_application_graph(app_root=str(tmp_path))

    # No fabricated generic App node was left behind.
    assert await App.get() is None


async def test_no_app_yaml_still_manual_bootstraps(tmp_path, test_db):
    App.clear_cache()
    # No app.yaml → manual bootstrap creates the App (via the guarded cache set).
    await bootstrap_application_graph(app_root=str(tmp_path))
    app = await App.get()
    assert app is not None
