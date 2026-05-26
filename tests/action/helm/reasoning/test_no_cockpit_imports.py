"""Invariant guard: no source-level coupling between Bridge and Cockpit.

The C-strategy hard constraint (BRIDGE-ROADMAP §C) forbids any ``.py`` file
under ``jvagent/action/helm/`` or ``jvagent/action/bridge/`` from importing
``jvagent.action.cockpit.*``. A future revision should be able to delete
``jvagent/action/cockpit/`` wholesale without breaking Bridge.

This test grep-scans the two packages and fails on any matching import line.
Docstring text that mentions "duplicated from jvagent/action/cockpit/..." is
allowed — that's attribution, not an import — and the regex below only
matches Python ``from`` / ``import`` statements.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

# Match real Python import statements only:
#   from jvagent.action.cockpit...
#   import jvagent.action.cockpit...
# A leading hash (comment) disqualifies the line.
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+jvagent\.action\.cockpit|import\s+jvagent\.action\.cockpit)\b"
)


def _scan_package(pkg_rel: str) -> list[str]:
    pkg = REPO_ROOT / pkg_rel
    if not pkg.exists():
        return []
    offenders: list[str] = []
    for path in pkg.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _IMPORT_RE.match(line):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
                )
    return offenders


def test_helm_package_has_no_cockpit_imports():
    offenders = _scan_package("jvagent/action/helm")
    assert not offenders, (
        "jvagent/action/helm/ must not import from jvagent.action.cockpit "
        "(BRIDGE-ROADMAP §C hard constraint). Offending lines:\n" + "\n".join(offenders)
    )


def test_bridge_package_has_no_cockpit_imports():
    offenders = _scan_package("jvagent/action/bridge")
    assert not offenders, (
        "jvagent/action/bridge/ must not import from jvagent.action.cockpit "
        "(BRIDGE-ROADMAP §C hard constraint). Offending lines:\n" + "\n".join(offenders)
    )
