"""bootstrap_application_graph runs the graph build under the distributed lease
(ADR-0033)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from jvagent.cli.bootstrap import bootstrap_application_graph
from jvagent.core.app import App

pytestmark = pytest.mark.asyncio


async def test_bootstrap_acquires_lease(tmp_path, monkeypatch, test_db):
    App.clear_cache()
    entered: list = []

    @asynccontextmanager
    async def _fake_lease(key, *, ttl=None):
        entered.append(key)
        yield

    # bootstrap imports distributed_lease from this module at call time.
    monkeypatch.setattr("jvagent.core.distributed_lease.distributed_lease", _fake_lease)

    # No app.yaml → manual bootstrap; still runs under the lease.
    await bootstrap_application_graph(app_root=str(tmp_path))

    assert entered, "bootstrap did not acquire the lease"
    assert entered[0].startswith("bootstrap:")
    # The manual bootstrap still produced the App inside the lease.
    assert await App.get() is not None
