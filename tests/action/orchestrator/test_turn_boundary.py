"""The shape of the tick loop, held to its size.

``_run_loop`` was 1090 lines in one scope. The first split moved preparation
out (``_prepare_turn`` → ``TurnState``); the second (audit follow-up S1) cut the
tick itself into typed steps — ``_tick`` → ``_tick_final`` / ``_tick_tool``
(``_guard_tool_call`` → ``_dispatch_tool`` → ``_after_dispatch``) — plus
``_after_loop`` and ``_close_turn``, every one of them reading and writing the
same ``TurnState`` instead of a shared scope.

These tests exist to stop that silently growing back. Each is a RATCHET: the
ceilings may go down, never up without a deliberate edit here and a reason in
the commit message.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from typing import Dict, List

from jvagent.action.orchestrator.turn_state import TurnState

LOOP_PY = pathlib.Path("jvagent/action/orchestrator/loop.py")

# The measured size at the S1 split. 45 names cross the prepare→tick boundary
# (drain_directive turned out to be dead and was dropped);
# the split moved the four values the tick carried between iterations as
# loop-locals (last_gear, last_dec_meta, last_obs_len, model_failures) onto the
# same object, because the steps that read them are now separate methods.
BOUNDARY_CEILING = 49  # 47 at M7 + parallel_batches (ADR-0048) = 48

# The methods that step a turn. Every one takes ``state: TurnState``; together
# they are the whole tick loop.
TICK_METHODS = (
    "_run_loop",
    "_tick",
    "_tick_final",
    "_tick_tool",
    "_guarded_siblings",
    "_dispatch_batch",
    "_guard_tool_call",
    "_dispatch_tool",
    "_after_dispatch",
    "_after_loop",
    "_close_turn",
)

# Per-method line ceilings, measured at the split with a little headroom. The
# point of the extraction is that no single step can quietly absorb the next
# five guards.
METHOD_CEILINGS: Dict[str, int] = {
    "_run_loop": 40,
    "_tick": 120,
    "_tick_final": 60,
    "_tick_tool": 30,
    # ADR-0048: sibling selection + guarding, and the concurrent dispatch, are
    # their own steps so the single-call path stays readable.
    "_guarded_siblings": 75,
    "_dispatch_batch": 60,
    "_guard_tool_call": 160,
    "_dispatch_tool": 110,
    "_after_dispatch": 210,
    "_after_loop": 90,
    "_close_turn": 40,
}


def _functions() -> Dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(LOOP_PY.read_text())
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }


def _function(name: str) -> ast.AsyncFunctionDef:
    fn = _functions().get(name)
    if fn is None:
        raise AssertionError(f"{name} not found in {LOOP_PY}")
    return fn


def _state_attributes_read_by(fns: List[ast.AsyncFunctionDef]) -> set:
    """Every ``state.X`` the functions touch (load or store)."""
    return {
        node.attr
        for fn in fns
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "state"
    }


def test_every_tick_method_exists_and_takes_the_state():
    fns = _functions()
    for name in TICK_METHODS:
        assert name in fns, f"{name} missing — the tick shape changed"
        params = {a.arg for a in fns[name].args.args}
        assert "visitor" in params, name
        if name != "_run_loop":
            assert "state" in params, f"{name} must step the shared TurnState"


def test_the_boundary_is_exactly_the_declared_turn_state():
    """What the tick methods touch and what TurnState declares must be the same
    set. A field nobody touches is dead weight; a name touched but undeclared
    would not survive import, but the first half is easy to miss in review."""
    declared = {f.name for f in dataclasses.fields(TurnState)}
    fns = _functions()
    touched = _state_attributes_read_by([fns[n] for n in TICK_METHODS])
    assert touched == declared, (
        f"declared-but-untouched: {sorted(declared - touched)}; "
        f"touched-but-undeclared: {sorted(touched - declared)}"
    )


def test_the_boundary_does_not_grow():
    """A ratchet, not a limit. Shrinking this list is the point of the split —
    each name removed is one less thing the steps share."""
    declared = dataclasses.fields(TurnState)
    assert len(declared) <= BOUNDARY_CEILING, (
        f"the turn state grew to {len(declared)} names (ceiling "
        f"{BOUNDARY_CEILING}). Adding to it couples the steps more tightly — if "
        "that is genuinely right, raise the ceiling in this test deliberately "
        "and say why."
    )


def test_preparation_can_end_the_turn_by_itself():
    """`_prepare_turn` returns Optional[TurnState]: None means the turn was
    already completed during preparation (a locked flow ran, or a drained task
    replied). If that contract is lost, those turns would fall through into the
    tick loop and answer twice."""
    fn = _function("_prepare_turn")
    returns_none = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
    ]
    assert len(returns_none) >= 2, "the early-exit paths out of preparation are gone"

    loop = _function("_run_loop")
    guards = [
        node
        for node in ast.walk(loop)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "state"
    ]
    assert guards, "_run_loop no longer checks whether preparation ended the turn"


def test_no_tick_method_grows_back_into_the_old_loop():
    """It was 1090 lines, then ~745 in one body. Each step now has its own
    ceiling so the next feature lands as a new step, not as fifty more lines in
    an existing one."""
    fns = _functions()
    for name, ceiling in METHOD_CEILINGS.items():
        fn = fns[name]
        length = (fn.end_lineno or 0) - fn.lineno
        assert length <= ceiling, f"{name} is {length} lines (ceiling {ceiling})"


def test_run_loop_stayed_smaller_than_it_was():
    """The original ratchet, kept: the driver is a driver."""
    fn = _function("_run_loop")
    length = (fn.end_lineno or 0) - fn.lineno
    assert length < 800, f"_run_loop is back up to {length} lines"
