"""Pilot TaskStore snapshots remain readable after a real worker restart."""

import os
import subprocess
import sys
import textwrap

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
