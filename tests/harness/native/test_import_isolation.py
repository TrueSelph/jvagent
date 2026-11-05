"""Cold native module imports must not initialize transitional execution engines."""

import subprocess
import sys
from pathlib import Path


def test_native_imports_do_not_load_legacy_execution() -> None:
    source = """
import sys
import jvagent.harness.native
from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.leadgen.leadgen_action import LeadGenAction
from jvagent.action.mcp.mcp_action import MCPAction
from jvagent.action.interview import InterviewAction
assert 'jvagent.tooling.tool_executor' not in sys.modules
assert 'jvagent.harness.runtime' not in sys.modules
assert 'jvagent.action.orchestrator.orchestrator_interact_action' not in sys.modules
assert 'jvagent.action.orchestrator.pilot.runtime' not in sys.modules
import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.jsondb import JsonDB
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.response.response_bus import ResponseBus
from jvagent.core.host_context import sign_host_system_context
from jvagent.memory.conversation import Conversation

async def exercise_shared_host() -> None:
    with tempfile.TemporaryDirectory() as directory:
        set_default_context(GraphContext(database=JsonDB(base_path=directory)))
        os.environ['JVSPATIAL_ENABLE_DEFERRED_SAVES'] = 'false'
        os.environ['JVAGENT_HOST_CONTEXT_SECRET'] = 'host-context-test-' * 4
        conversation = Conversation(session_id='session', user_id='user')
        await conversation.save()
        bus = ResponseBus()
        bus.native_execution = True
        memory = SimpleNamespace(get_session=AsyncMock(return_value=(
            None, conversation, 'user', 'session', False)))
        host = SimpleNamespace(
            id='agent', get_memory=AsyncMock(return_value=memory),
            get_response_bus=AsyncMock(return_value=bus),
            get_actions=AsyncMock(return_value=[NativeHarnessAction()]),
            get_access_control_action=AsyncMock(return_value=None))
        data = sign_host_system_context(
            'Trusted host policy', agent_id='agent', user_id='user',
            session_id='session', run_id='host-request')
        walker = InteractWalker(agent_id='agent', user_id='user',
            session_id='session', utterance='hello', data=data)
        result = await walker.initialize_interaction(host)
        assert result.ok, result
        assert walker._host_system_context == 'Trusted host policy'
        ledger = walker.conversation.context['_jvagent_host_context_nonces']
        assert list(ledger.values())[0]['correlation_id'] == walker.correlation_id
        assert walker.correlation_id != 'host-request'
        await bus.publish(session_id='session', content='hello',
            channel='default',
            interaction=walker.interaction, interaction_id=walker.interaction.id,
            user_id='user')
        await walker._finalize()
        replay = InteractWalker(agent_id='agent', user_id='user',
            session_id='session', utterance='', data=data)
        rejected = await replay.initialize_interaction(host)
        assert not rejected.ok
        assert replay._host_system_context is None
        assert replay.interaction is None
        memory.get_session.return_value = (None, conversation, '', 'session', False)
        unidentified = InteractWalker(agent_id='agent', utterance='hello')
        try:
            await unidentified.initialize_interaction(host)
        except RuntimeError as exc:
            assert 'resolved caller identity' in str(exc)
        else:
            raise AssertionError('Native admission accepted empty caller identity')
        assert unidentified.interaction is None
        assert 'jvagent.harness.runtime' not in sys.modules
        assert 'jvagent.tooling.tool_executor' not in sys.modules
        assert 'jvagent.action.orchestrator.turn_cache' not in sys.modules

asyncio.run(exercise_shared_host())
from jvagent.harness import HarnessRuntime
from jvagent.harness.runtime import HarnessRuntime as original
assert HarnessRuntime is original
from jvagent.tooling import ToolExecutionEngine
from jvagent.tooling.tool_executor import ToolExecutionEngine as legacy_executor
assert ToolExecutionEngine is legacy_executor
from jvagent.action.orchestrator import OrchestratorInteractAction
from jvagent.action.orchestrator.orchestrator_interact_action import OrchestratorInteractAction as legacy_action
assert OrchestratorInteractAction is legacy_action
assert 'jvagent.action.orchestrator.endpoints' in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
