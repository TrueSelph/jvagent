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
    )
