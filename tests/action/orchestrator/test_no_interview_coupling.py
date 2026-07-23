"""Guard: the orchestrator carries no interview-specific literals.

Allowlist: intentional ADR-0034 touchpoints in continuation.py (soft-abandon via
apply_abandon) and skill_tasks.py (_clear_interview_session for prerequisite push
session cleanup). All other orchestrator modules must remain interview-agnostic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parents[3] / "jvagent/action/orchestrator"
_PATTERN = re.compile(r"interview__|interview_action|set_field")

# Intentional interview touchpoints (ADR-0034 abandonment + prerequisite cleanup).
_ALLOWED = {
    "continuation.py": {
        "from jvagent.action.interview.reaper import apply_abandon",
        "await apply_abandon(conversation, store, handle, spec)",
        "from jvagent.action.interview import tasks as interview_tasks",
    },
    "skill_tasks.py": {
        "await bound._clear_interview_session(visitor)",
        "if hasattr(bound, '_clear_interview_session'):",
    },
}


@pytest.mark.parametrize("path", sorted(_ORCH.glob("*.py")), ids=lambda p: p.name)
def test_orchestrator_module_has_no_interview_literals(path):
    text = path.read_text(encoding="utf-8")
    allowed_for_file = _ALLOWED.get(path.name, set())
    offenders = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if not _PATTERN.search(line):
            continue
        stripped = line.strip()
        if stripped not in allowed_for_file:
            offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, "interview coupling remains:\n" + "\n".join(offenders)
