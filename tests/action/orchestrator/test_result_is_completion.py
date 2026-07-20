"""_result_is_completion: the loop's silent-completion detector (ADR-0026 drain).

A prerequisite skill can finish with a result that carries the completion flags
but no user-facing ``response_directive`` of its own (a "silent" completion).
The loop must still detect it to re-resolve the task lock and resume a now-runnable
parent in the same turn — so the detector reads only the completion flags.
"""

from __future__ import annotations

import json

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

_is_completion = OrchestratorInteractAction._result_is_completion


def test_detects_interview_complete_flag():
    obs = json.dumps({"ok": True, "status": "completed", "interview_complete": True})
    assert _is_completion(obs) is True


def test_detects_status_completed_without_flag():
    assert _is_completion(json.dumps({"status": "completed"})) is True


def test_silent_completion_no_directive():
    # No response_directive at all — still a completion.
    obs = json.dumps({"ok": True, "interview_complete": True, "results": []})
    assert _is_completion(obs) is True


def test_not_a_completion():
    assert _is_completion(json.dumps({"ok": True, "status": "ok"})) is False
    assert (
        _is_completion(json.dumps({"response_directive": "Tell the user: hi"})) is False
    )


def test_non_json_or_non_dict_is_false():
    assert _is_completion("not json") is False
    assert _is_completion(json.dumps(["a", "b"])) is False
    assert _is_completion("") is False


_last_dir = OrchestratorInteractAction._last_activation_directive


def test_last_activation_directive_extracts_from_skill_session_note():
    obs = [
        {"tool": "other", "observation": "x"},
        {
            "tool": "(skill-session)",
            "observation": json.dumps(
                {
                    "status": "extraction_pending",
                    "response_directive": "Tell the user: working on it",
                }
            ),
        },
    ]
    assert _last_dir(obs) == "Tell the user: working on it"


def test_last_activation_directive_prefers_most_recent():
    obs = [
        {
            "tool": "(skill-session)",
            "observation": json.dumps({"response_directive": "old"}),
        },
        {
            "tool": "(skill-session)",
            "observation": json.dumps({"response_directive": "new"}),
        },
    ]
    assert _last_dir(obs) == "new"


def test_last_activation_directive_empty_when_absent_or_unparsable():
    assert _last_dir([{"tool": "reply", "observation": "hi"}]) == ""
    assert _last_dir([{"tool": "(skill-session)", "observation": "not json"}]) == ""
    assert (
        _last_dir(
            [{"tool": "(skill-session)", "observation": json.dumps({"ok": True})}]
        )
        == ""
    )
    assert _last_dir([]) == ""
