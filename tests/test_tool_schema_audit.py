"""Repo-wide audit: every Tool(parameters_schema=...) must be portable.

Strict-mode model providers (OpenAI gpt-4.1 + Structured Outputs, Anthropic's
strict tool definitions, etc.) reject schemas with multi-type ``type`` arrays
or ``type: array`` without ``items``. This test scans every ``Tool(...)``
construction site in the repo and asserts each schema is clean per
``jvagent.tooling.tool_schema_validator``.

Failures here will name the offending file:line, tool name, and the exact
JSON-Pointer-like path of the violation.
"""

from __future__ import annotations

import ast
import os
from typing import List, Tuple

from jvagent.tooling.tool_schema_validator import validate_parameters_schema

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE_DIR = os.path.join(REPO_ROOT, "jvagent")

_SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", "build"}


def _iter_python_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _find_tool_schemas(
    path: str,
) -> List[Tuple[int, str, dict]]:
    """Return [(lineno, tool_name, schema_dict), ...] for Tool(...) calls in ``path``."""
    found: List[Tuple[int, str, dict]] = []
    try:
        tree = ast.parse(open(path).read())
    except SyntaxError:
        return found

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # We're matching any Tool(...) constructor call — including subclasses.
        # The robust signal is the kwarg presence of ``parameters_schema`` and
        # ``name`` (or ``execute``).
        tool_name = None
        schema = None
        for kw in getattr(node, "keywords", []):
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                tool_name = kw.value.value
            if kw.arg == "parameters_schema" and isinstance(kw.value, ast.Dict):
                try:
                    schema = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError):
                    schema = None
        if isinstance(schema, dict):
            found.append((node.lineno, tool_name or "<unknown>", schema))
    return found


def test_repo_tool_schemas_are_strict_provider_clean():
    """Every Tool(...) in the repo must have a strict-provider-clean schema."""
    failures: List[str] = []
    tools_audited = 0

    for path in _iter_python_files(SOURCE_DIR):
        for lineno, name, schema in _find_tool_schemas(path):
            tools_audited += 1
            issues = validate_parameters_schema(schema)
            if issues:
                rel = os.path.relpath(path, REPO_ROOT)
                detail = "; ".join(f"{p}: {m}" for p, m in issues)
                failures.append(f"{rel}:{lineno} tool={name!r} — {detail}")

    assert tools_audited > 0, "No Tool(...) schemas found — audit didn't run"
    assert not failures, (
        f"\n{len(failures)} tool schema(s) violate strict-provider rules:\n"
        + "\n".join("  " + f for f in failures)
    )


def test_validator_catches_multi_type_arrays():
    issues = validate_parameters_schema(
        {
            "type": "object",
            "properties": {
                "x": {"type": ["string", "object"]},
            },
        }
    )
    assert issues
    assert any("multi-type" in m or "list" in m for _, m in issues)


def test_validator_catches_array_without_items():
    issues = validate_parameters_schema(
        {
            "type": "object",
            "properties": {
                "tags": {"type": "array"},
            },
        }
    )
    assert issues
    assert any("items" in m for _, m in issues)


def test_validator_passes_on_well_formed_schema():
    issues = validate_parameters_schema(
        {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "matrix": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                },
            },
            "required": ["key"],
        }
    )
    assert not issues


def test_validator_flags_required_referencing_unknown():
    issues = validate_parameters_schema(
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x", "missing_property"],
        }
    )
    assert issues
    assert any("unknown" in m for _, m in issues)
