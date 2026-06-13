"""Server-prep visualization keys off a generic marker, not interview__."""

from __future__ import annotations

import inspect

from jvagent.action.orchestrator import skill_tasks
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


def test_session_note_observation_carries_server_prep_marker():
    obs: list = []
    skill_tasks._append_session_note(obs, "bootstrap note")
    assert obs[-1]["kind"] == "server_prep"
    assert obs[-1]["observation"] == "bootstrap note"


def test_emitter_filters_on_marker_not_namespace():
    src = inspect.getsource(OrchestratorInteractAction._emit_server_prep_tool_thoughts)
    assert "server_prep" in src
    assert "interview__" not in src
