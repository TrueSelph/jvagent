"""Guard: action packages must not import sibling action families directly."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_ACTION_ROOT = _REPO / "jvagent" / "action"

# Infrastructure / shared packages — not action-family boundaries.
_SHARED_ALLOWLIST = frozenset(
    {
        "utils",
        "interact",
        "model",
        "oauth",
        "channels",
        "skill_spec",
        "manifest",
        "parameters",
        "loader",
        "reply",
        "response",
        "access_control",
        "orchestrator",
        "spreadsheet",
        "agent_utils",
        "code_execution",
        "file_interface",
        "task_monitor",
        "streaming",
        "plugin_contracts",
    }
)

# Documented sibling edges (legacy coupling — prefer shared modules for new code).
_ALLOWED_SIBLING_EDGES = frozenset(
    {
        ("email_action", "google"),
        ("email_action", "microsoft"),
        ("google", "email_action"),
        ("leadgen", "mcp"),
        ("microsoft", "email_action"),
        ("pageindex", "google"),
    }
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+jvagent\.action\.(\w+)|import\s+jvagent\.action\.(\w+))",
    re.MULTILINE,
)

# Paths excluded from the guard (tests may target specific integrations).
_EXCLUDE_PREFIXES = ("tests/",)


def _action_packages() -> frozenset[str]:
    names: set[str] = set()
    for path in _ACTION_ROOT.iterdir():
        if path.is_dir() and not path.name.startswith("_"):
            names.add(path.name)
    return frozenset(names - _SHARED_ALLOWLIST)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for path in _ACTION_ROOT.rglob("*.py"):
        rel = str(path.relative_to(_REPO))
        if any(rel.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        files.append(path)
    return files


def test_no_sibling_action_imports() -> None:
    """Fail when an action package imports another action family outside the allowlist."""
    packages = _action_packages()
    offenders: list[str] = []

    for path in _source_files():
        try:
            rel_parts = path.relative_to(_ACTION_ROOT).parts
        except ValueError:
            continue
        if not rel_parts:
            continue
        source_pkg = rel_parts[0]
        if source_pkg not in packages:
            continue

        text = path.read_text(encoding="utf-8")
        for match in _IMPORT_RE.finditer(text):
            target = match.group(1) or match.group(2)
            if not target or target in _SHARED_ALLOWLIST:
                continue
            if target == source_pkg:
                continue
            if target not in packages:
                continue
            if (source_pkg, target) in _ALLOWED_SIBLING_EDGES:
                continue
            rel = str(path.relative_to(_REPO))
            offenders.append(f"{rel}: imports jvagent.action.{target}")

    assert not offenders, (
        "Sibling action imports detected (use shared modules — see action-authoring §15):\n"
        + "\n".join(sorted(offenders))
    )
