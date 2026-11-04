"""Native startup must not initialize transitional execution engines."""

import subprocess
import sys
from pathlib import Path


def test_native_imports_do_not_load_legacy_execution() -> None:
    source = """
import sys
import jvagent.harness.native
from jvagent.action.native_harness import NativeHarnessAction
assert 'jvagent.harness.runtime' not in sys.modules
assert 'jvagent.action.orchestrator.orchestrator_interact_action' not in sys.modules
assert 'jvagent.action.orchestrator.pilot.runtime' not in sys.modules
from jvagent.harness import HarnessRuntime
from jvagent.harness.runtime import HarnessRuntime as original
assert HarnessRuntime is original
"""
    subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        capture_output=True,
        text=True,
    )
