"""Guard: PersonaAction is retired (ADR-0025).

No jvagent source module imports `jvagent.action.persona` or references
PersonaAction — ReplyAction is the single output contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "jvagent"
_PATTERN = re.compile(r"jvagent\.action\.persona|import PersonaAction|PersonaAction\(")


@pytest.mark.parametrize(
    "path", sorted(_SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(_SRC))
)
def test_no_persona_import(path):
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _PATTERN.search(line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, "PersonaAction reference remains:\n" + "\n".join(offenders)
