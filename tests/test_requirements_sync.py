"""The requirements files must match ``[project] dependencies`` in pyproject.

``pyproject.toml`` is the source of truth for what jvagent needs at runtime, but
two requirements files are what actually get installed in places pip's metadata
never reaches:

- ``Dockerfile.base`` builds the runtime image from ``requirements-all.txt``.
- ``requirements.txt`` is what the README and runbooks tell people to install.

Nothing kept them in step, and they drifted: both sat two jvspatial releases
behind, and both were missing four core dependencies outright (aiohttp, pymupdf,
packaging, mcp), so ``pip install -r requirements.txt`` produced an install that
could not load several actions. The failure is silent at install time and only
shows up as an ImportError deep in a run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
REQUIREMENTS = ("requirements.txt", "requirements-all.txt")

_REQUIREMENT_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]+\])?)\s*(.*)$")


def _canonical(name: str) -> str:
    """PEP 503 normalization, minus any extras marker."""
    return re.sub(r"[-_.]+", "-", name.split("[")[0]).lower()


def _core_dependencies() -> Dict[str, str]:
    """``{canonical_name: full_spec}`` from ``[project] dependencies``."""
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
    assert block, "could not locate [project] dependencies in pyproject.toml"
    deps: Dict[str, str] = {}
    for raw in re.findall(r'"([^"]+)"', block.group(1)):
        match = _REQUIREMENT_RE.match(raw)
        assert match, f"unparsable dependency spec: {raw!r}"
        deps[_canonical(match.group(1))] = raw.strip()
    return deps


def _listed(filename: str) -> Dict[str, str]:
    """``{canonical_name: full_spec}`` for one requirements file."""
    listed: Dict[str, str] = {}
    for line in (REPO_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT_RE.match(line)
        if match:
            listed[_canonical(match.group(1))] = line
    return listed


def test_core_dependencies_are_listed() -> None:
    """Every runtime dependency must appear in both requirements files."""
    core = _core_dependencies()
    missing: List[str] = []
    for filename in REQUIREMENTS:
        listed = _listed(filename)
        for name in core:
            if name not in listed:
                missing.append(f"{filename}: {core[name]}")
    assert not missing, "missing from requirements files:\n  " + "\n  ".join(missing)


def test_core_dependency_specs_match() -> None:
    """A listed dependency must carry the same version spec as pyproject.

    A requirements file pinning an older jvspatial than pyproject declares is
    how the Docker image and the tested tree end up on different versions.
    """
    core = _core_dependencies()
    mismatched: List[str] = []
    for filename in REQUIREMENTS:
        for name, spec in _listed(filename).items():
            expected = core.get(name)
            if expected is not None and spec != expected:
                mismatched.append(f"{filename}: {spec!r} != pyproject {expected!r}")
    assert not mismatched, "version specs out of sync:\n  " + "\n  ".join(mismatched)
