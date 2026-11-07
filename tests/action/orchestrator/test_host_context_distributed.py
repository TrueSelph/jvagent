"""Opt-in shared-store replay qualification for signed host context.

Set JVAGENT_TEST_POSTGRES_DSN and JVAGENT_TEST_REDIS_URL to disposable
services. The test uses independent processes and the production graph/lock
implementations; it does not create a second replay database.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import queue
from types import SimpleNamespace
from typing import Any

import pytest

_POSTGRES_DSN_ENV = "JVAGENT_TEST_POSTGRES_DSN"
_REDIS_URL_ENV = "JVAGENT_TEST_REDIS_URL"
_HOST_CONTEXT_SECRET = "distributed-host-context-test-key-32-bytes"
_CALLER = {
    "agent_id": "n.Agent.distributed-host-context-test",
    "user_id": "o.User.distributed-host-context-test",
    "session_id": "distributed-host-context-test-session",
}


def _consume_host_context_in_process(
    name: str,
    dsn: str,
    redis_url: str,
    conversation_id: str,
    envelope: dict[str, Any],
    barrier: Any,
    results: Any,
) -> None:
    os.environ["JVSPATIAL_POSTGRES_DSN"] = dsn
    os.environ["JVSPATIAL_DB_TYPE"] = "postgres"
    os.environ["JVAGENT_CONVERSATION_LOCK_REDIS_URL"] = redis_url
    os.environ["JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"] = "5"
    os.environ["JVAGENT_HOST_CONTEXT_SECRET"] = _HOST_CONTEXT_SECRET

    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.postgres import PostgresDB

    from jvagent.action.orchestrator.host_context import consume_host_system_context
    from jvagent.memory.conversation import Conversation

    async def run() -> None:
        database = PostgresDB(dsn=dsn, min_size=0, max_size=3)
        set_default_context(GraphContext(database=database))
        try:
            # Resolve both process-local snapshots before either consumes the
            # nonce, reproducing two workers admitted from stale graph reads.
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            barrier.wait(timeout=20)
            visitor = SimpleNamespace(
                data=envelope,
                conversation=conversation,
                correlation_id=f"corr-{name}",
                **_CALLER,
            )
            consumed = await consume_host_system_context(visitor)
            results.put((name, consumed == "trusted context"))
        finally:
            await database.close()

    asyncio.run(run())


@pytest.mark.skipif(
    not os.environ.get(_POSTGRES_DSN_ENV) or not os.environ.get(_REDIS_URL_ENV),
    reason=(
        "set JVAGENT_TEST_POSTGRES_DSN and JVAGENT_TEST_REDIS_URL to run "
        "shared graph replay qualification"
    ),
)
def test_host_context_nonce_is_single_use_across_processes_and_shared_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("asyncpg")
    pytest.importorskip("redis.asyncio")

    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.postgres import PostgresDB

    from jvagent.action.orchestrator.host_context import (
        _REPLAY_KEY,
        sign_host_system_context,
    )
    from jvagent.memory.conversation import Conversation

    dsn = os.environ[_POSTGRES_DSN_ENV]
    redis_url = os.environ[_REDIS_URL_ENV]
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", _HOST_CONTEXT_SECRET)
    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", redis_url)
    envelope = sign_host_system_context(
        "trusted context",
        **_CALLER,
        run_id="distributed-run",
        nonce=f"nonce-{os.urandom(12).hex()}",
        ttl_seconds=300,
    )

    async def create_conversation() -> str:
        database = PostgresDB(dsn=dsn, min_size=0, max_size=3)
        set_default_context(GraphContext(database=database))
        try:
            conversation = await Conversation.create(
                session_id=_CALLER["session_id"] + "-" + os.urandom(6).hex(),
                user_id=_CALLER["user_id"],
                channel="default",
            )
            return str(conversation.id)
        finally:
            await database.close()

    conversation_id = asyncio.run(create_conversation())
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_consume_host_context_in_process,
            args=(name, dsn, redis_url, conversation_id, envelope, barrier, results),
        )
        for name in ("first", "second")
    ]
    for worker in workers:
        worker.start()

    try:
        received = [results.get(timeout=30), results.get(timeout=30)]
    except queue.Empty:
        pytest.fail("workers did not both report host-context consumption")
    finally:
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()
        results.join_thread()

    assert [worker.exitcode for worker in workers] == [0, 0]
    assert sorted(consumed for _, consumed in received) == [False, True]

    async def read_ledger() -> dict[str, Any]:
        database = PostgresDB(dsn=dsn, min_size=0, max_size=2)
        set_default_context(GraphContext(database=database))
        try:
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            ledger = conversation.context.get(_REPLAY_KEY, {})
            assert isinstance(ledger, dict)
            return ledger
        finally:
            await database.close()

    ledger = asyncio.run(read_ledger())
    nonce = json.loads(envelope["host_system_context"]["body"])["nonce"]
    assert list(ledger) == [nonce]
    assert ledger[nonce]["correlation_id"] in {"corr-first", "corr-second"}
