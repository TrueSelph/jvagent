"""Separate-worker crash-boundary qualification for pilot effect receipts.

The SQLite service below is deliberately test-only. It models an effect system
whose durable effect record can be inspected after the worker dies; it does not
stand in for a production approval or effect service.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

from jvagent.memory.conversation import Conversation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary, expected_exit, expected_invocation, expected_effect",
    [
        ("before_effect", 71, "prepared", None),
        ("after_effect_before_receipt", 72, "started", ("pending", "")),
        ("after_settled_snapshot", 73, "settled", ("complete", "written")),
    ],
)
async def test_effect_crash_boundaries_are_inspectable_after_worker_restart(
    test_db,
    tmp_path,
    boundary,
    expected_exit,
    expected_invocation,
    expected_effect,
):
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.jsondb import JsonDB

    graph_path = tmp_path / "graph"
    graph_path.mkdir()
    set_default_context(GraphContext(database=JsonDB(base_path=str(graph_path))))
    conversation = await Conversation.create(
        session_id=f"pilot-crash-{boundary}",
        user_id="pilot-crash-user",
        channel="default",
    )
    sqlite_path = tmp_path / "effects.sqlite"
    worker = textwrap.dedent(
        """
        import asyncio
        import os
        import sqlite3
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.jsondb import JsonDB
        from jvagent.action.orchestrator.pilot.contracts import (
            PilotCaller, PilotInvocation, PilotSnapshot,
        )
        from tests.action.orchestrator.pilot.effect_state_fixture import (
            PilotEffectTestStore,
        )
        from jvagent.memory.conversation import Conversation

        async def main():
            graph_path, conversation_id, sqlite_path, boundary = sys.argv[1:]
            set_default_context(GraphContext(database=JsonDB(base_path=graph_path)))
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            caller = PilotCaller(
                agent_id="pilot-crash-agent",
                user_id="pilot-crash-user",
                session_id=f"pilot-crash-{boundary}",
            )
            state = PilotEffectTestStore(conversation)
            snapshot = PilotSnapshot(
                caller=caller,
                skill_id="test_effect",
                skill_digest="crash-skill-sha256",
                config_digest="crash-config-sha256",
                question="Exercise a crash boundary.",
            )
            handle = await state.create(
                snapshot, task_id="pilot_crash_task", title="Crash test",
                description="Test-only effect recovery boundary",
            )
            invocation = PilotInvocation(
                invocation_id="pilot-crash-invocation",
                tool_name="fake_service__write",
                payload_digest="crash-payload-sha256",
            )
            snapshot = await state.record_invocation(handle, snapshot, invocation)
            if boundary == "before_effect":
                os._exit(71)

            snapshot = await state.mark_invocation_started(
                handle, snapshot, invocation.invocation_id
            )
            with sqlite3.connect(sqlite_path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS effects "
                    "(invocation_id TEXT PRIMARY KEY, state TEXT, result TEXT)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS business_effects "
                    "(invocation_id TEXT PRIMARY KEY, value TEXT)"
                )
                db.execute(
                    "INSERT INTO effects VALUES (?, 'pending', '')",
                    (invocation.invocation_id,),
                )
                db.execute(
                    "INSERT INTO business_effects VALUES (?, 'written')",
                    (invocation.invocation_id,),
                )
                if boundary == "after_effect_before_receipt":
                    db.commit()
                    os._exit(72)
                db.execute(
                    "UPDATE effects SET state='complete', result='written' "
                    "WHERE invocation_id=?",
                    (invocation.invocation_id,),
                )
            await state.settle_invocation(
                handle, snapshot, invocation.invocation_id, "written"
            )
            os._exit(73)

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(graph_path),
            str(conversation.id),
            str(sqlite_path),
            boundary,
        ],
        cwd=os.getcwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert result.returncode == expected_exit, result.stderr

    inspector = textwrap.dedent(
        """
        import asyncio
        import json
        import sqlite3
        import sys
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.jsondb import JsonDB
        from jvagent.action.orchestrator.pilot.contracts import PilotCaller
        from tests.action.orchestrator.pilot.effect_state_fixture import (
            PilotEffectTestStore as PilotTaskStore,
        )
        from jvagent.action.orchestrator.pilot.state import PilotStateError
        from jvagent.memory.conversation import Conversation

        async def main():
            graph_path, conversation_id, sqlite_path, boundary = sys.argv[1:]
            set_default_context(GraphContext(database=JsonDB(base_path=graph_path)))
            conversation = await Conversation.get(conversation_id)
            assert conversation is not None
            caller = PilotCaller(
                agent_id="pilot-crash-agent",
                user_id="pilot-crash-user",
                session_id=f"pilot-crash-{boundary}",
            )
            state = PilotTaskStore(conversation)
            handle, snapshot = state.load(
                "pilot_crash_task", caller=caller, skill_id="test_effect",
                skill_digest="crash-skill-sha256",
                config_digest="crash-config-sha256",
            )
            with sqlite3.connect(sqlite_path) as db:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='effects'"
                ).fetchone()
                effect = None
                business_effect = None
                if exists:
                    effect = db.execute(
                        "SELECT state, result FROM effects WHERE invocation_id=?",
                        ("pilot-crash-invocation",),
                    ).fetchone()
                    business_effect = db.execute(
                        "SELECT value FROM business_effects WHERE invocation_id=?",
                        ("pilot-crash-invocation",),
                    ).fetchone()
            if boundary == "after_effect_before_receipt":
                await state.require_reconciliation(
                    handle, snapshot, "worker exited after effect before receipt"
                )
                try:
                    await state.resume(
                        handle, caller=caller,
                        skill_id="test_effect",
                        skill_digest=snapshot.skill_digest,
                        config_digest=snapshot.config_digest,
                    )
                except PilotStateError:
                    resume_refused = True
                else:
                    resume_refused = False
            else:
                resume_refused = None
            print(json.dumps({
                "task_status": handle.status,
                "snapshot_status": (
                    "parked" if boundary == "after_effect_before_receipt"
                    else snapshot.status
                ),
                "invocation_status": snapshot.invocations[0].status,
                "invocation_result": snapshot.invocations[0].result,
                "effect": effect,
                "business_effect": business_effect,
                "resume_refused": resume_refused,
            }, sort_keys=True))

        asyncio.run(main())
        """
    )
    inspected = subprocess.run(
        [
            sys.executable,
            "-c",
            inspector,
            str(graph_path),
            str(conversation.id),
            str(sqlite_path),
            boundary,
        ],
        cwd=os.getcwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert inspected.returncode == 0, inspected.stderr
    observed = json.loads(inspected.stdout)
    assert observed["invocation_status"] == expected_invocation
    assert observed["effect"] == (
        list(expected_effect) if expected_effect is not None else None
    )
    if expected_effect is None:
        assert observed["business_effect"] is None
    else:
        assert observed["business_effect"] == ["written"]
    if boundary == "after_effect_before_receipt":
        assert observed["task_status"] == "parked"
        assert observed["snapshot_status"] == "parked"
        assert observed["resume_refused"] is True
    elif boundary == "after_settled_snapshot":
        assert observed["task_status"] == "active"
        assert observed["invocation_result"] == "written"

    await conversation.delete(cascade=True)
