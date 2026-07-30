"""The seam between preparing a turn and stepping it, held to its size.

``_run_loop`` was 1090 lines in one scope. Preparation and stepping shared 82
locals, so the interface between them was not documented anywhere — it was
whatever the two halves happened to touch. Splitting them made that interface
explicit: 47 names, carried by ``TurnState``.

These tests exist to stop it silently growing back. A 48th name is not forbidden,
but it should be a decision someone makes on purpose, not something that appears
because a variable was convenient to reach.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

from jvagent.action.orchestrator.turn_state import TurnState

LOOP_PY = pathlib.Path("jvagent/action/orchestrator/loop.py")

# The measured size at the split. This is a RATCHET: it may go down, never up
# without a deliberate edit here and a reason in the commit message.
BOUNDARY_CEILING = 47


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(LOOP_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {LOOP_PY}")


def _state_attributes_read_by(fn: ast.AsyncFunctionDef) -> set:
    """Every ``state.X`` the function reads."""
    return {
        node.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "state"
    }


def test_the_boundary_is_exactly_the_declared_turn_state():
    """What the loop unpacks and what TurnState declares must be the same set.

    A field nobody reads is dead weight; a name read but undeclared would not
    survive import, but the first half of that is easy to miss in review.
    """
    declared = {f.name for f in dataclasses.fields(TurnState)}
    read = _state_attributes_read_by(_function("_run_loop"))
    assert read == declared, (
        f"declared-but-unread: {sorted(declared - read)}; "
        f"read-but-undeclared: {sorted(read - declared)}"
    )


def test_the_boundary_does_not_grow():
    """A ratchet, not a limit. Shrinking this list is the point of the split —
    each name removed is one less thing preparation and stepping share."""
    declared = dataclasses.fields(TurnState)
    assert len(declared) <= BOUNDARY_CEILING, (
        f"the turn boundary grew to {len(declared)} names (ceiling "
        f"{BOUNDARY_CEILING}). Adding to it couples preparation and stepping "
        "more tightly — if that is genuinely right, lower the ceiling in this "
        "test deliberately and say why."
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


def test_run_loop_stayed_smaller_than_it_was():
    """It was 1090 lines. The split moved ~390 out; this stops that being
    quietly undone by the next feature."""
    fn = _function("_run_loop")
    length = (fn.end_lineno or 0) - fn.lineno
    assert length < 800, f"_run_loop is back up to {length} lines"
