"""The prompt must be a function of configuration, not of interpreter luck.

The harness builds its tool surface out of sets and dicts. Set iteration order
in CPython depends on ``PYTHONHASHSEED``, which is randomised per process. If
any of that ordering reached the rendered prompt, the same agent with the same
configuration would send a different prompt on every restart — and since prompt
order changes model behaviour, the agent would be subtly non-reproducible in a
way no single-process test could ever show.

This renders the same turn in two separate interpreters with different seeds and
compares hashes. It is the only form of this test that is worth anything.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.wire._probe import write_app

pytestmark = pytest.mark.asyncio


def _render(app_root: str, seed: str) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["JVSPATIAL_ENABLE_DEFERRED_SAVES"] = "false"
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.run(
        [sys.executable, "-m", "tests.wire._render_once", app_root],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.getcwd(),
        timeout=300,
    )
    out = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in ("SYSTEM", "USER"):
            out[parts[0]] = (parts[1], int(parts[2]))
    if not out:
        raise AssertionError(
            f"render subprocess produced nothing (rc={proc.returncode})\n"
            f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )
    return out


async def test_the_prompt_is_identical_under_different_hash_seeds(tmp_path):
    app_root = write_app(tmp_path)
    first = _render(app_root, "0")
    second = _render(app_root, "987654")

    assert first["SYSTEM"] == second["SYSTEM"], (
        "system prompt differs between interpreters — something order-dependent "
        f"reaches the model: {first['SYSTEM']} vs {second['SYSTEM']}"
    )
    assert first["USER"] == second["USER"], "user turn differs between interpreters"
    # Guard against the degenerate pass where both renders are empty.
    assert first["SYSTEM"][1] > 500
    assert first["USER"][1] > 100


async def test_repeated_renders_in_one_process_are_identical(wire):
    """Cheap companion to the cross-process check: catches per-tick state that
    leaks between renders (a cache that accumulates, a list appended to twice)."""
    first = await wire.capture("what can you do?", block_raw_tool_invocation=True)
    second = await wire.capture("what can you do?", block_raw_tool_invocation=True)
    assert first.system == second.system
    assert first.user == second.user
