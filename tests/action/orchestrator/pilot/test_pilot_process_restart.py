"""Pilot TaskStore snapshots remain readable after a real worker restart."""

import json
import os
import subprocess
import sys
import textwrap
import uuid

import pytest

from jvagent.action.orchestrator.pilot.contracts import (
    EvidenceReference,
    PilotCaller,
    PilotSnapshot,
)
from jvagent.action.orchestrator.pilot.state import PilotTaskStore
from jvagent.memory.conversation import Conversation


@pytest.mark.asyncio
async def test_pilot_snapshot_reloads_in_a_separate_process(tmp_path):
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.jsondb import JsonDB

    database_path = tmp_path / "pilot-restart-db"
    database_path.mkdir()
    set_default_context(GraphContext(database=JsonDB(base_path=str(database_path))))
    conversation = await Conversation.create(
        session_id="pilot-process-restart",
        user_id="pilot-restart-user",
        channel="default",
    )
    caller = PilotCaller(
        agent_id="pilot-restart-agent",
        user_id="pilot-restart-user",
        session_id="pilot-process-restart",
    )
    state = PilotTaskStore(conversation)
    handle = await state.create(
        PilotSnapshot(
            caller=caller,
            skill_id="research",
            skill_digest="restart-skill-sha256",
            config_digest="restart-config-sha256",
            question="Can this survive a process restart?",
        ),
        task_id="pilot_process_restart",
        title="Restart proof",
        description="Read after a worker restart",
    )
    persisted = PilotSnapshot(
        caller=caller,
        skill_id="research",
        skill_digest="restart-skill-sha256",
        config_digest="restart-config-sha256",
        question="Can this survive a process restart?",
        evidence=(
            EvidenceReference(
                source_id="https://example.test/restart",
                url="https://example.test/restart",
                title="Restart source",
                excerpt="Persisted before worker exit.",
            ),
        ),
    )
    await state.save(handle, persisted)

    reader = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.jsondb import JsonDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller
        from jvagent.action.orchestrator.pilot.state import PilotTaskStore
        from jvagent.memory.conversation import Conversation

        async def main():
            set_default_context(GraphContext(database=JsonDB(base_path=sys.argv[1])))
            conversation = await Conversation.get(sys.argv[2])
            assert conversation is not None
            caller = PilotCaller(
                agent_id="pilot-restart-agent",
                user_id="pilot-restart-user",
                session_id="pilot-process-restart",
            )
            handle, snapshot = PilotTaskStore(conversation).load(
                "pilot_process_restart",
                caller=caller,
                skill_id="research",
                skill_digest="restart-skill-sha256",
                config_digest="restart-config-sha256",
            )
            print(json.dumps({
                "status": handle.status,
                "question": snapshot.question,
                "source_id": snapshot.evidence[0].source_id,
            }, sort_keys=True))

        asyncio.run(main())
        """
    )
    environment = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-c", reader, str(database_path), str(conversation.id)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "active"' in result.stdout
    assert '"question": "Can this survive a process restart?"' in result.stdout
    assert '"source_id": "https://example.test/restart"' in result.stdout
    await conversation.delete(cascade=True)


@pytest.mark.skipif(
    not os.environ.get("JVAGENT_TEST_POSTGRES_DSN"),
    reason="set JVAGENT_TEST_POSTGRES_DSN to a disposable PostgreSQL database",
)
def test_active_pilot_checkpoint_recovers_after_postgres_worker_crash():
    """A fresh process can discover uncertain usage in the graph TaskStore."""

    pytest.importorskip("asyncpg")
    dsn = os.environ["JVAGENT_TEST_POSTGRES_DSN"]
    test_id = uuid.uuid4().hex
    session_id = f"pilot-pg-crash-{test_id}"
    task_id = f"pilot_pg_crash_{test_id}"
    writer = textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.postgres import PostgresDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller, PilotSnapshot
        from jvagent.action.orchestrator.pilot.state import PilotTaskStore
        from jvagent.memory.conversation import Conversation

        async def main():
            dsn, session_id, task_id = sys.argv[1:]
            database = PostgresDB(dsn=dsn, min_size=0, max_size=2)
            set_default_context(GraphContext(database=database))
            conversation = await Conversation.create(
                session_id=session_id,
                user_id="pilot-pg-crash-user",
                channel="default",
            )
            caller = PilotCaller(
                agent_id="pilot-pg-crash-agent",
                user_id="pilot-pg-crash-user",
                session_id=session_id,
            )
            state = PilotTaskStore(conversation)
            handle = await state.create(
                PilotSnapshot(
                    caller=caller,
                    skill_id="research",
                    skill_digest="postgres-crash-skill",
                    config_digest="postgres-crash-config",
                    question="Recover this interrupted PostgreSQL checkpoint.",
                    model_requests_used=1,
                    unsettled_model_requests=1,
                    usage_accounting_complete=False,
                ),
                task_id=task_id,
                title="PostgreSQL crash checkpoint",
                description="Persist before an abrupt worker exit",
            )
            print(f"CONVERSATION_ID={conversation.id}", flush=True)
            os._exit(71)

        asyncio.run(main())
        """
    )
    created = subprocess.run(
        [sys.executable, "-c", writer, dsn, session_id, task_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert created.returncode == 71, created.stderr
    conversation_id = next(
        (
            line.removeprefix("CONVERSATION_ID=").strip()
            for line in created.stdout.splitlines()
            if line.startswith("CONVERSATION_ID=")
        ),
        "",
    )
    assert conversation_id, created.stdout

    reader = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.postgres import PostgresDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller
        from jvagent.action.orchestrator.pilot.state import PilotTaskStore
        from jvagent.memory.conversation import Conversation

        async def main():
            dsn, conversation_id, session_id, task_id = sys.argv[1:]
            database = PostgresDB(dsn=dsn, min_size=0, max_size=2)
            set_default_context(GraphContext(database=database))
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            caller = PilotCaller(
                agent_id="pilot-pg-crash-agent",
                user_id="pilot-pg-crash-user",
                session_id=session_id,
            )
            state = PilotTaskStore(conversation)
            handle, snapshot = state.active_run(
                caller=caller,
                skill_id="research",
                skill_digest="postgres-crash-skill",
                config_digest="postgres-crash-config",
            )
            assert handle is not None
            print("RECOVERED=" + json.dumps({
                "task_id": handle.id,
                "task_status": handle.status,
                "snapshot_status": snapshot.status,
                "question": snapshot.question,
                "model_requests_used": snapshot.model_requests_used,
                "unsettled_model_requests": snapshot.unsettled_model_requests,
                "usage_accounting_complete": snapshot.usage_accounting_complete,
            }, sort_keys=True))
            await conversation.delete(cascade=True)
            await database.close()

        asyncio.run(main())
        """
    )
    inspected = subprocess.run(
        [sys.executable, "-c", reader, dsn, conversation_id, session_id, task_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert inspected.returncode == 0, inspected.stderr
    recovery_line = next(
        line.removeprefix("RECOVERED=")
        for line in inspected.stdout.splitlines()
        if line.startswith("RECOVERED=")
    )
    recovered = json.loads(recovery_line)
    assert recovered == {
        "task_id": task_id,
        "task_status": "active",
        "snapshot_status": "running",
        "question": "Recover this interrupted PostgreSQL checkpoint.",
        "model_requests_used": 1,
        "unsettled_model_requests": 1,
        "usage_accounting_complete": False,
    }


@pytest.mark.skipif(
    not os.environ.get("JVAGENT_TEST_POSTGRES_DSN"),
    reason="set JVAGENT_TEST_POSTGRES_DSN to a disposable PostgreSQL database",
)
def test_cancelled_pilot_checkpoint_survives_postgres_worker_exit():
    """A committed cancellation remains terminal and readable after worker exit."""

    pytest.importorskip("asyncpg")
    dsn = os.environ["JVAGENT_TEST_POSTGRES_DSN"]
    test_id = uuid.uuid4().hex
    session_id = f"pilot-pg-cancel-{test_id}"
    task_id = f"pilot_pg_cancel_{test_id}"
    writer = textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.postgres import PostgresDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller, PilotSnapshot
        from jvagent.action.orchestrator.pilot.state import PilotTaskStore
        from jvagent.memory.conversation import Conversation

        async def main():
            dsn, session_id, task_id = sys.argv[1:]
            database = PostgresDB(dsn=dsn, min_size=0, max_size=2)
            set_default_context(GraphContext(database=database))
            conversation = await Conversation.create(
                session_id=session_id,
                user_id="pilot-pg-cancel-user",
                channel="default",
            )
            caller = PilotCaller(
                agent_id="pilot-pg-cancel-agent",
                user_id="pilot-pg-cancel-user",
                session_id=session_id,
            )
            state = PilotTaskStore(conversation)
            snapshot = PilotSnapshot(
                caller=caller,
                skill_id="research",
                skill_digest="postgres-cancel-skill",
                config_digest="postgres-cancel-config",
                question="Persist a terminal cancellation on PostgreSQL.",
                model_requests_used=1,
                unsettled_model_requests=1,
                usage_accounting_complete=False,
            )
            handle = await state.create(
                snapshot,
                task_id=task_id,
                title="PostgreSQL cancellation checkpoint",
                description="Cancel before abrupt worker exit",
            )
            cancelled = snapshot.model_copy(update={"status": "cancelled"})
            await state.cancel(handle, cancelled, "operator cancelled")
            print(f"CONVERSATION_ID={conversation.id}", flush=True)
            os._exit(72)

        asyncio.run(main())
        """
    )
    created = subprocess.run(
        [sys.executable, "-c", writer, dsn, session_id, task_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert created.returncode == 72, created.stderr
    conversation_id = next(
        (
            line.removeprefix("CONVERSATION_ID=").strip()
            for line in created.stdout.splitlines()
            if line.startswith("CONVERSATION_ID=")
        ),
        "",
    )
    assert conversation_id, created.stdout

    reader = textwrap.dedent(
        """
        import asyncio
        import json
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.postgres import PostgresDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller
        from jvagent.action.orchestrator.pilot.state import PilotTaskStore
        from jvagent.memory.conversation import Conversation

        async def main():
            dsn, conversation_id, session_id, task_id = sys.argv[1:]
            database = PostgresDB(dsn=dsn, min_size=0, max_size=2)
            set_default_context(GraphContext(database=database))
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            caller = PilotCaller(
                agent_id="pilot-pg-cancel-agent",
                user_id="pilot-pg-cancel-user",
                session_id=session_id,
            )
            state = PilotTaskStore(conversation)
            handle, snapshot = state.load(
                task_id,
                caller=caller,
                skill_id="research",
                skill_digest="postgres-cancel-skill",
                config_digest="postgres-cancel-config",
            )
            assert state.active_run(
                caller=caller,
                skill_id="research",
                skill_digest="postgres-cancel-skill",
                config_digest="postgres-cancel-config",
            ) is None
            print("CANCELLED=" + json.dumps({
                "task_id": handle.id,
                "task_status": handle.status,
                "snapshot_status": snapshot.status,
                "question": snapshot.question,
                "model_requests_used": snapshot.model_requests_used,
                "unsettled_model_requests": snapshot.unsettled_model_requests,
                "usage_accounting_complete": snapshot.usage_accounting_complete,
            }, sort_keys=True))
            await conversation.delete(cascade=True)
            await database.close()

        asyncio.run(main())
        """
    )
    inspected = subprocess.run(
        [sys.executable, "-c", reader, dsn, conversation_id, session_id, task_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert inspected.returncode == 0, inspected.stderr
    cancelled_line = next(
        line.removeprefix("CANCELLED=")
        for line in inspected.stdout.splitlines()
        if line.startswith("CANCELLED=")
    )
    cancelled_state = json.loads(cancelled_line)
    assert cancelled_state == {
        "task_id": task_id,
        "task_status": "cancelled",
        "snapshot_status": "cancelled",
        "question": "Persist a terminal cancellation on PostgreSQL.",
        "model_requests_used": 1,
        "unsettled_model_requests": 1,
        "usage_accounting_complete": False,
    }
