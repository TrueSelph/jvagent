"""Guard: the orchestrator carries no interview-specific literals."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parents[3] / "jvagent/action/orchestrator"
_PATTERN = re.compile(r"interview__|interview_action|set_field")


@pytest.mark.parametrize("path", sorted(_ORCH.glob("*.py")), ids=lambda p: p.name)
def test_orchestrator_module_has_no_interview_literals(path):
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _PATTERN.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, "interview coupling remains:\n" + "\n".join(offenders)
