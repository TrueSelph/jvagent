"""Abrupt native worker exit against a real disposable JSON graph."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.core.context import (
    GraphContext,
    clear_default_context,
    set_default_context,
)
from jvspatial.db.jsondb import JsonDB
from pydantic_ai_harness.step_persistence import continue_run

from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "tool"])
async def test_library_records_survive_abrupt_worker_exit(
    tmp_path: Path, monkeypatch: Any, mode: str
) -> None:
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    source = Path(__file__).resolve().parents[3]
    env = dict(os.environ, PYTHONPATH=str(source), PYDANTIC_AI_NO_BANNER="1")
    worker = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("process_fixture.py")),
            str(tmp_path),
            mode,
        ],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert worker.returncode == 17, worker.stderr
    set_default_context(
        GraphContext(database=JsonDB(base_path=str(tmp_path / "graph")))
    )
    try:
        interaction = await Interaction.get((tmp_path / "interaction-id").read_text())
        assert interaction is not None
        store = InteractionStepStore(interaction)
        assert await store.get_run(run_id="process-run")
        if mode == "complete":
            messages = await continue_run(store, run_id="process-run")
            assert len(messages) == 2
            assert not await store.list_unresolved_tool_effects(run_id="process-run")
        else:
            effects = await store.list_unresolved_tool_effects(run_id="process-run")
            assert len(effects) == 1
            assert effects[0].tool_name == "crash"
            assert effects[0].status == "started"
            # The host must reconcile that unknown effect before replay.
            with pytest.raises(LookupError):
                await continue_run(store, run_id="process-run")
    finally:
        clear_default_context()
