"""Opt-in real PostgreSQL qualification through public graph/Harness interfaces."""

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
pytest.importorskip("asyncpg")
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)
from jvspatial.db.postgres import PostgresDB
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.spend import (
    Budget,
    BudgetStatus,
    SpendEntry,
    SpendSnapshot,
    Spent,
)
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from jvagent.harness.accounting import NativeSpendRecorder
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("reported", [False, True])
@pytest.mark.parametrize("embedded", [False, True])
async def test_http_cancellation_terminal_event_survives_postgres_flush(
    postgres_scope: "PostgresScope",
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
    reported: bool,
    embedded: bool,
) -> None:
    from tests.harness.native.test_cancellation import (
        test_http_disconnect_preserves_sdk_terminal_cleanup,
    )

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    await test_http_disconnect_preserves_sdk_terminal_cleanup(
        monkeypatch,
        tmp_path,
        "responses",
        True,
        reported=reported,
        embedded=embedded,
    )
    rows = await postgres_scope.database.find("node", {"entity": "Interaction"})
    assert len(rows) == 1
    assert rows[0]["context"]["native_steps"]["events"][-1]["kind"] == "run_failed"


@dataclass
class PostgresScope:
    database: PostgresDB
    admin: Any = field(repr=False)
    schema: str
    dsn: str = field(repr=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_interview_cleanup_retains_durable_host_replay_protection(
    postgres_scope: PostgresScope,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
) -> None:
    from jvagent.core.host_context import HOST_CONTEXT_REPLAY_KEY
    from tests.action.interact.test_native_host_context import (
        test_interview_cleanup_does_not_reopen_consumed_host_nonce,
    )

    await test_interview_cleanup_does_not_reopen_consumed_host_nonce(
        monkeypatch, deferred
    )
    rows = await postgres_scope.database.find("node", {"entity": "Conversation"})
    assert len(rows) == 1
    assert len(rows[0]["context"]["context"][HOST_CONTEXT_REPLAY_KEY]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_native_interview_workflow_has_durable_domain_and_effect_state(
    postgres_scope: PostgresScope,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
) -> None:
    from tests.harness.native.test_interview import (
        test_native_interview_validation_review_and_later_confirmation,
    )

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    await test_native_interview_validation_review_and_later_confirmation(tmp_path)
    rows = await postgres_scope.database.find("node", {"entity": "Conversation"})
    assert len(rows) == 1
    state = rows[0]["context"]
    assert "interview" not in state.get("context", {})
    assert state["tasks"][0]["status"] == "completed"
    assert "state" not in state["tasks"][0]["data"]
    interactions = await postgres_scope.database.find("node", {"entity": "Interaction"})
    assert len(interactions) == 2
    effects = [
        e for row in interactions for e in row["context"]["native_steps"]["effects"]
    ]
    assert len(effects) == 6
    assert all(e["status"] == "completed" for e in effects)


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_native_branch_pivot_and_review_bindings_persist(
    postgres_scope: PostgresScope,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
) -> None:
    from tests.harness.native.test_interview_branches import (
        test_native_branch_pivot_preserves_shared_fields_and_rebinds_review,
    )

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    await test_native_branch_pivot_preserves_shared_fields_and_rebinds_review(tmp_path)
    rows = await postgres_scope.database.find("node", {"entity": "Conversation"})
    assert len(rows) == 1
    assert rows[0]["context"]["tasks"][0]["status"] == "completed"
    assert "interview" not in rows[0]["context"].get("context", {})
    interactions = await postgres_scope.database.find("node", {"entity": "Interaction"})
    assert len(interactions) == 3
    effects = [
        effect
        for row in interactions
        for effect in row["context"]["native_steps"]["effects"]
    ]
    assert len(effects) == 7
    assert all(effect["status"] == "completed" for effect in effects)


@pytest.fixture
async def postgres_scope(
    request: pytest.FixtureRequest,
) -> AsyncIterator[PostgresScope]:
    dsn = os.environ.get("JVAGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "Set JVAGENT_TEST_POSTGRES_DSN for disposable PostgreSQL qualification"
        )
    asyncpg = pytest.importorskip("asyncpg")
    admin = await asyncpg.connect(dsn, timeout=5)
    schema = "native_qualification_" + uuid.uuid4().hex
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    database = PostgresDB(
        dsn=dsn,
        schema_name=schema,
        min_size=1,
        max_size=2,
        command_timeout=getattr(request, "param", 10.0),
    )
    previous = get_default_context()
    set_default_context(GraphContext(database=database))
    try:
        yield PostgresScope(database=database, admin=admin, schema=schema, dsn=dsn)
    finally:
        set_default_context(previous)
        await database.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_native_records_and_spend_survive_postgres_context_reload(
    postgres_scope: PostgresScope,
) -> None:
    assert postgres_scope.database.command_timeout == 10
    conversation = await Conversation.create(user_id="caller", session_id="session")
    interaction = await conversation.add_interaction(utterance="Hello")
    agent = Agent(
        TestModel(call_tools=[]),
        capabilities=[
            StepPersistence(
                store=InteractionStepStore(interaction), capture_frontier=True
            )
        ],
    )
    result = await agent.run("Hello", run_id="postgres-run", conversation_id="session")
    store = ConversationSpendStore(conversation)
    entry = SpendEntry(
        key="session",
        usd=Decimal("0.01"),
        tokens=10,
        requests=1,
        token="synthetic-fixture",
    )
    await store.add_many([entry, entry])
    await postgres_scope.database.close()
    fresh_database = PostgresDB(
        dsn=postgres_scope.dsn, schema_name=postgres_scope.schema, command_timeout=10
    )
    try:
        set_default_context(GraphContext(database=fresh_database))
        restored = await Interaction.get(interaction.id)
        snapshot = await InteractionStepStore(restored).latest_snapshot(
            run_id="postgres-run"
        )
        assert snapshot.messages == result.all_messages()
        assert (
            await InteractionStepStore(restored).list_events(run_id="postgres-run")
        )[-1].kind == "run_completed"
        restored_conversation = await Conversation.get(conversation.id)
        counters = await ConversationSpendStore(restored_conversation).get_many(
            ["session"]
        )
        assert counters["session"].usd == Decimal("0.01")
        assert counters["session"].requests == 1
    finally:
        await fresh_database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("postgres_scope", [0.2], indirect=True)
@pytest.mark.parametrize(
    "kind", ["registration", "checkpoint", "snapshot", "effect", "spend_receipt"]
)
async def test_postgres_timeout_does_not_turn_retry_into_false_ack(
    postgres_scope: PostgresScope, kind: str
) -> None:
    assert postgres_scope.database.command_timeout == 0.2
    interaction = await Interaction.create(utterance="locked write")
    if kind in {"registration", "checkpoint", "snapshot", "effect"}:
        store = InteractionStepStore(interaction)
        event = StepEvent(
            run_id="run",
            kind="model_request_started",
            step_index=1,
            idempotency_key="start",
        )

        if kind == "registration":
            record = RunRecord(run_id="run", conversation_id="session")
            operation, key = store.register_run, "runs"
        elif kind == "snapshot":
            record = ContinuableSnapshot(
                run_id="run", step_index=1, messages=[], idempotency_key="snapshot"
            )
            operation, key = store.save_snapshot, "snapshots"
        elif kind == "effect":
            record = ToolEffectRecord(
                run_id="run", tool_call_id="call", tool_name="read", status="started"
            )
            operation, key = store.record_tool_effect, "effects"
        else:
            record, operation, key = event, store.append_event, "events"

        async def write() -> None:
            await operation(record)

        def values(raw: dict[str, Any]) -> list[Any]:
            return raw["context"].get("native_steps", {}).get(key, [])

    else:
        recorder = NativeSpendRecorder(interaction)
        event = SpendSnapshot(
            model="gpt-4.1",
            usage=RequestUsage(input_tokens=10, output_tokens=5),
            usd=Decimal("0.001"),
            priced=True,
            budgets=(
                BudgetStatus(
                    budget=Budget(window="run"),
                    key="run",
                    spent=Spent(requests=1),
                    remaining_usd=None,
                    remaining_tokens=None,
                    warning=False,
                    exhausted=False,
                ),
            ),
        )

        async def write() -> None:
            with recorder.bind(SimpleNamespace(run_id="run", run_step=1)):
                await recorder.record(event)

        def values(raw: dict[str, Any]) -> list[Any]:
            return raw["context"].get("observability_metrics", [])

    await postgres_scope.admin.execute("BEGIN")
    try:
        await postgres_scope.admin.execute(
            f'LOCK TABLE "{postgres_scope.schema}".node IN ACCESS EXCLUSIVE MODE'
        )
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await write()
        # A cached mutation is not a durable acknowledgement. Retrying while
        # the lock is still held must attempt storage and fail again.
        with pytest.raises(asyncio.TimeoutError):
            await write()
        assert time.monotonic() - started < 2
        raw = await postgres_scope.admin.fetchval(
            f'SELECT data FROM "{postgres_scope.schema}".node WHERE id = $1',
            interaction.id,
        )
        assert not values(json.loads(raw) if isinstance(raw, str) else raw)
    finally:
        await postgres_scope.admin.execute("ROLLBACK")
    # A client timeout does not prove a write cannot finish after unlock.
    # Require the explicit retry to acknowledge one durable record instead.
    await write()
    assert len(values(await postgres_scope.database.get("node", interaction.id))) == 1
