"""Guard: the orchestrator carries no interview-specific literals.

Thin-harness invariants 6 and 8: the foundation is domain-agnostic. A task-lock
plugin reaches the orchestrator only through duck-typed bound-action hooks
(``task_lock_runtime_ready``, ``prepare_task_lock_turn``, ``task_lock_abandon``,
``clear_task_lock_session``, …) and by registering its result vocabulary at
load (``register_task_completion_flag`` / ``register_task_lock_skill_key`` /
``register_trusted_directive_prefix``). Any direct import of the interview
package, lookup of ``InterviewAction``, or interview envelope key in orchestrator
source is a regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parents[3] / "jvagent/action/orchestrator"
_PATTERN = re.compile(
    r"interview__|interview_action|set_field|jvagent\.action\.interview"
    r"|InterviewAction|interview_complete|interview_type|_clear_interview_session"
    r"|resolve_interview_spec"
)


@pytest.mark.parametrize("path", sorted(_ORCH.glob("*.py")), ids=lambda p: p.name)
def test_orchestrator_module_has_no_interview_literals(path):
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _PATTERN.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, "interview coupling remains:\n" + "\n".join(offenders)
