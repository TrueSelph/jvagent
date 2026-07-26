"""Wire-contract tier: assert on what actually reaches the model.

The rest of the suite constructs actions in memory. That is not the thing which
serves traffic, and the bugs that motivated this tier all lived in the gap: a
persisted attribute silently outranking a new code default, a transport that
skipped governance, a harness referencing attributes that no longer existed.
Three thousand passing unit tests saw none of them.

So this tier does the one thing they cannot: it bootstraps a real app graph from
YAML, loads the orchestrator **back out of the database**, and captures the
exact system/user prompt a tick would send. Only the model is stubbed, so
nothing is billed and nothing leaves the process.

Each test bootstraps its own app (~1s). That is deliberate: jvspatial objects
bind to the event loop that created them, and a session-scoped graph shared
across pytest-asyncio's per-test loops produces "attached to different loop" —
see the trap table in the root CLAUDE.md.
"""

from __future__ import annotations

import pytest

from tests.wire._probe import WireProbe, load_orchestrator, write_app


@pytest.fixture
async def wire(tmp_path, monkeypatch):
    """A WireProbe over an orchestrator loaded back out of a real graph."""
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    app_root = write_app(tmp_path)
    set_app_root(app_root)
    try:
        yield WireProbe(await load_orchestrator(app_root))
    finally:
        clear_app_root()
