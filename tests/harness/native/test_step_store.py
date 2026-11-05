"""Public StepPersistence integration and graph serialization boundary tests."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import StepStore

from jvagent.harness.step_store import InteractionStepStore


@pytest.mark.asyncio
async def test_native_step_persistence_round_trip() -> None:
    disk = {}
    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        disk.update(json.loads(json.dumps(interaction.native_steps)))

    interaction.save = save
    store = InteractionStepStore(interaction)
    assert isinstance(store, StepStore)
    agent = Agent(TestModel(call_tools=[]), capabilities=[StepPersistence(store=store)])
    result = await agent.run("Hello", run_id="native-one", conversation_id="session")
    restored = InteractionStepStore(SimpleNamespace(native_steps=disk))
    assert (await restored.get_run(run_id="native-one")).conversation_id == "session"
    snapshot = await restored.latest_snapshot(run_id="native-one")
    assert snapshot is not None
    assert snapshot.messages == result.all_messages()
    assert await restored.list_events(run_id="native-one")
    assert not await restored.list_unresolved_tool_effects(run_id="native-one")
    assert [r.run_id for r in await restored.list_runs(conversation_id="session")] == [
        "native-one"
    ]


@pytest.mark.asyncio
async def test_step_event_idempotency_and_scope() -> None:
    from pydantic_ai_harness.step_persistence import StepEvent

    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        pass

    interaction.save = save
    store = InteractionStepStore(interaction)
    event = StepEvent(
        run_id="one", kind="run_started", step_index=0, idempotency_key="first"
    )
    await store.append_event(event)
    await store.append_event(event)
    assert len(await store.list_events(run_id="one")) == 1
    assert not await store.list_events(run_id="other")


@pytest.mark.asyncio
async def test_unresolved_effects_and_interrupted_frontier() -> None:
    from pydantic_ai_harness.step_persistence import (
        ContinuableSnapshot,
        ToolEffectRecord,
    )

    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        pass

    interaction.save = save
    store = InteractionStepStore(interaction)
    await store.record_tool_effect(
        ToolEffectRecord(
            run_id="run",
            tool_call_id="call",
            tool_name="lookup",
            status="started",
        )
    )
    assert len(await store.list_unresolved_tool_effects(run_id="run")) == 1
    await store.record_tool_effect(
        ToolEffectRecord(
            run_id="run",
            tool_call_id="call",
            tool_name="lookup",
            status="completed",
        )
    )
    assert not await store.list_unresolved_tool_effects(run_id="run")
    await store.save_snapshot(
        ContinuableSnapshot(run_id="run", step_index=1, messages=[])
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run",
            step_index=2,
            messages=[],
            state="interrupted",
        )
    )
    assert (await store.latest_snapshot(run_id="run")).step_index == 1
    assert (
        await store.latest_snapshot(run_id="run", include_interrupted=True)
    ).step_index == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_step_snapshot_survives_fresh_graph_context(
    tmp_path: Any, monkeypatch: Any, deferred: bool
) -> None:
    from jvspatial.core.context import (
        GraphContext,
        clear_default_context,
        set_default_context,
    )
    from jvspatial.db.jsondb import JsonDB

    from jvagent.memory.interaction import Interaction

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    path = str(tmp_path / "graph")
    set_default_context(GraphContext(database=JsonDB(base_path=path)))
    try:
        interaction = Interaction(utterance="Hello")
        await interaction.save()
        store = InteractionStepStore(interaction)
        agent = Agent(
            TestModel(call_tools=[]), capabilities=[StepPersistence(store=store)]
        )
        result = await agent.run("Hello", run_id="graph-run", conversation_id="session")
        identity = interaction.id
        set_default_context(GraphContext(database=JsonDB(base_path=path)))
        reloaded = await Interaction.get(identity)
        assert reloaded is not None
        snapshot = await InteractionStepStore(reloaded).latest_snapshot(
            run_id="graph-run"
        )
        assert snapshot is not None
        assert snapshot.messages == result.all_messages()
        assert (
            len(await InteractionStepStore(reloaded).list_events(run_id="graph-run"))
            > 1
        )
    finally:
        clear_default_context()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["registration", "event"])
async def test_retry_after_failed_write_persists_cached_record(kind: str) -> None:
    from pydantic_ai_harness.step_persistence import RunRecord, StepEvent

    disk: dict[str, Any] = {}
    failures = 1
    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("unavailable store")
        disk.update(json.loads(json.dumps(interaction.native_steps)))

    interaction.save = save
    store = InteractionStepStore(interaction)
    if kind == "registration":
        record = RunRecord(run_id="run", conversation_id="session")
        key, method = "runs", "register_run"
    else:
        record = StepEvent(
            run_id="run", kind="run_started", step_index=0, idempotency_key="start"
        )
        key, method = "events", "append_event"
    with pytest.raises(OSError):
        await getattr(store, method)(record)
    assert not disk
    # A new wrapper around the same cached entity must not assume a committed write.
    restored_wrapper = InteractionStepStore(interaction)
    await getattr(restored_wrapper, method)(record)
    assert len(disk[key]) == 1
    await getattr(restored_wrapper, method)(record)
    assert len(disk[key]) == 1
