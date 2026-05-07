"""Tests for the skill-tool module-load cache.

The cockpit's skill tool loader caches the expensive parts of importing a
skill source file (file I/O, ``importlib`` exec, ``inspect.signature``)
keyed on ``(absolute_path, mtime)``. Each cockpit run rebuilds only the
slim per-call wrapper closure that captures the current visitor.
"""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jvagent.action.cockpit.registry import assembler as registry_mod


def _make_skill_file(tmp_path: Path, body: str) -> Path:
    skill_file = tmp_path / "skill_tool.py"
    skill_file.write_text(textwrap.dedent(body), encoding="utf-8")
    return skill_file


_HAPPY_BODY = """
    def get_tool_definition():
        return {
            "name": "demo_tool",
            "description": "test tool",
            "parameters": {"type": "object", "properties": {}},
        }

    def execute(args, visitor=None):
        return f"called: {args}"
"""


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Each test starts with a clean module cache."""
    registry_mod.clear_skill_module_cache()
    yield
    registry_mod.clear_skill_module_cache()


def test_first_load_misses_cache_subsequent_loads_hit(tmp_path):
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)

    cached_a = registry_mod._load_or_get_cached_module(skill_file, "demo")
    cached_b = registry_mod._load_or_get_cached_module(skill_file, "demo")

    # Same instance — cache hit.
    assert cached_a is cached_b
    assert cached_a.raw_tool_name == "demo_tool"
    assert cached_a.execute_takes_visitor is True
    assert cached_a.skip_reason is None


def test_mtime_change_invalidates_cache(tmp_path):
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)
    cached_a = registry_mod._load_or_get_cached_module(skill_file, "demo")

    # Bump mtime by editing the file (different content, different mtime).
    new_body = _HAPPY_BODY.replace('"demo_tool"', '"demo_tool_v2"')
    skill_file.write_text(textwrap.dedent(new_body), encoding="utf-8")
    # Ensure mtime differs (some filesystems quantise to whole seconds).
    new_mtime = skill_file.stat().st_mtime + 2
    os.utime(skill_file, (new_mtime, new_mtime))

    cached_b = registry_mod._load_or_get_cached_module(skill_file, "demo")
    assert cached_a is not cached_b
    assert cached_b.raw_tool_name == "demo_tool_v2"


def test_cache_records_skip_reason_for_unusable_modules(tmp_path):
    """Modules missing required hooks cache a skip_reason — diagnostic work
    isn't repeated."""
    skill_file = tmp_path / "broken.py"
    skill_file.write_text(
        textwrap.dedent(
            """
            def get_tool_definition():
                return {"name": "x", "parameters": {}}
            # No execute() function defined.
            """
        ),
        encoding="utf-8",
    )

    cached_a = registry_mod._load_or_get_cached_module(skill_file, "demo")
    cached_b = registry_mod._load_or_get_cached_module(skill_file, "demo")

    assert cached_a is cached_b  # cache hit
    assert cached_a.skip_reason is not None
    assert "execute" in cached_a.skip_reason


def test_load_tool_module_uses_cache_via_public_path(tmp_path):
    """The public ``_load_tool_module`` returns identical schema across calls.

    Different ctx instances (per-call) produce DIFFERENT wrapper closures but
    the same name / description / parameters_schema (which come from the cache).
    """
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)
    ctx_a = MagicMock(visitor=MagicMock())
    ctx_b = MagicMock(visitor=MagicMock())

    tool_a, reason_a = registry_mod._load_tool_module(skill_file, "demo", set(), ctx_a)
    tool_b, reason_b = registry_mod._load_tool_module(skill_file, "demo", set(), ctx_b)

    assert reason_a is None and reason_b is None
    assert tool_a is not tool_b  # fresh Tool per call (per-call closure)
    assert tool_a.name == tool_b.name == "demo__demo_tool"
    assert tool_a.description == tool_b.description == "test tool"
    # Same schema dict by value (cache returns the same reference, actually).
    assert tool_a.parameters_schema == tool_b.parameters_schema


def test_warm_load_is_dramatically_faster_than_cold(tmp_path):
    """Operational signal: warm cache reads should be at least 5× faster than cold."""
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)

    iters = 50

    # Cold: clear cache between every call.
    t0 = time.perf_counter()
    for _ in range(iters):
        registry_mod.clear_skill_module_cache()
        registry_mod._load_or_get_cached_module(skill_file, "demo")
    cold_elapsed = time.perf_counter() - t0

    # Warm: prime once, then repeated lookups.
    registry_mod.clear_skill_module_cache()
    registry_mod._load_or_get_cached_module(skill_file, "demo")
    t0 = time.perf_counter()
    for _ in range(iters):
        registry_mod._load_or_get_cached_module(skill_file, "demo")
    warm_elapsed = time.perf_counter() - t0

    # Warm is dramatically faster — actual ratio in CI is 50–100×, allow 5× lower
    # bound to stay stable across machines.
    assert warm_elapsed * 5 < cold_elapsed, (
        f"warm={warm_elapsed*1000:.3f}ms cold={cold_elapsed*1000:.3f}ms "
        "expected warm < cold/5"
    )


def test_clear_skill_module_cache_drops_all_entries(tmp_path):
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)
    registry_mod._load_or_get_cached_module(skill_file, "demo")
    assert len(registry_mod._SKILL_MODULE_CACHE) >= 1

    registry_mod.clear_skill_module_cache()
    assert len(registry_mod._SKILL_MODULE_CACHE) == 0


def test_load_tool_module_allows_qualified_name_in_allowed_tools(tmp_path):
    """``allowed_tools`` accepts the qualified ``{prefix}__{name}`` form.

    Skill authors sometimes declare allowed-tools using the cockpit-qualified
    name (the form the model sees). The loader matches either form so the
    declaration is forgiving.
    """
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)

    # Raw name = "demo_tool". Qualified = "demo__demo_tool". Caller passes only
    # the qualified form — loader must still accept it.
    tool, reason = registry_mod._load_tool_module(
        skill_file,
        "demo",
        allowed_tools={"demo__demo_tool"},
        ctx=MagicMock(visitor=MagicMock()),
    )
    assert reason is None
    assert tool is not None
    assert tool.name == "demo__demo_tool"

    # Conversely, raw form alone also works.
    tool2, reason2 = registry_mod._load_tool_module(
        skill_file,
        "demo",
        allowed_tools={"demo_tool"},
        ctx=MagicMock(visitor=MagicMock()),
    )
    assert reason2 is None
    assert tool2 is not None


def test_load_tool_module_rejects_when_neither_form_in_allowed_tools(tmp_path):
    """If allowed_tools is set and contains NEITHER raw nor qualified, reject."""
    skill_file = _make_skill_file(tmp_path, _HAPPY_BODY)

    tool, reason = registry_mod._load_tool_module(
        skill_file,
        "demo",
        allowed_tools={"some_other_tool"},
        ctx=MagicMock(visitor=MagicMock()),
    )
    assert tool is None
    assert reason is not None
    assert "not in allowed_tools" in reason
    # Diagnostic message should reference both forms checked.
    assert "demo_tool" in reason
    assert "demo__demo_tool" in reason


def test_cached_skill_module_execute_takes_visitor_is_resolved_once(tmp_path):
    """``execute_takes_visitor`` is computed at cache-miss time, not per call."""
    body_no_visitor = """
        def get_tool_definition():
            return {
                "name": "no_visitor",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }

        def execute(args):
            return "ok"
    """
    skill_file = _make_skill_file(tmp_path, body_no_visitor)

    cached = registry_mod._load_or_get_cached_module(skill_file, "demo")
    assert cached.execute_takes_visitor is False
