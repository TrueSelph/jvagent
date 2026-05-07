"""Tests for ``jvagent.action.cockpit.session.CockpitSession`` and helpers."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.cockpit.session import (
    SESSION_KEY,
    CockpitSession,
    clear_session,
    get_session,
    get_session_optional,
)


def _visitor_with_state(state=None) -> SimpleNamespace:
    """Build a stub visitor with a mutable ``_skill_state`` dict."""
    return SimpleNamespace(_skill_state=state if state is not None else {})


# ----------------------------------------------------------------------
# CockpitSession dataclass
# ----------------------------------------------------------------------


def test_session_defaults_are_falsy() -> None:
    s = CockpitSession()
    assert s.engine is None
    assert s.interaction_id is None
    assert s.debug_state is None
    assert s.pending_interact_actions == []
    assert s.ia_finalize_pending is False
    assert s.finalized is False
    assert s.trace_task_id is None
    assert s.model_planned is False


def test_session_reset_in_place_preserves_identity() -> None:
    s = CockpitSession()
    original_id = id(s)
    s.engine = "fake_engine"
    s.interaction_id = "int_42"
    s.debug_state = {"k": "v"}
    s.pending_interact_actions.append("ia")
    s.ia_finalize_pending = True
    s.finalized = True
    s.trace_task_id = "task_99"
    s.model_planned = True

    s.reset()

    assert id(s) == original_id  # same instance — callers' references stay valid
    assert s.engine is None
    assert s.interaction_id is None
    assert s.debug_state is None
    assert s.pending_interact_actions == []
    assert s.ia_finalize_pending is False
    assert s.finalized is False
    assert s.trace_task_id is None
    assert s.model_planned is False


# ----------------------------------------------------------------------
# get_session
# ----------------------------------------------------------------------


def test_get_session_creates_on_first_access() -> None:
    visitor = _visitor_with_state()
    sess = get_session(visitor)
    assert isinstance(sess, CockpitSession)
    # Stored at canonical key.
    assert visitor._skill_state[SESSION_KEY] is sess


def test_get_session_returns_same_instance_on_repeat() -> None:
    visitor = _visitor_with_state()
    sess_a = get_session(visitor)
    sess_a.interaction_id = "stable"
    sess_b = get_session(visitor)
    assert sess_a is sess_b
    assert sess_b.interaction_id == "stable"


def test_get_session_creates_skill_state_when_absent() -> None:
    """Visitor without ``_skill_state`` → helper allocates it lazily."""
    visitor = SimpleNamespace()  # no _skill_state attr
    sess = get_session(visitor)
    assert isinstance(sess, CockpitSession)
    assert isinstance(visitor._skill_state, dict)
    assert visitor._skill_state[SESSION_KEY] is sess


def test_get_session_replaces_non_session_value_at_key() -> None:
    """Non-session value (e.g. legacy dict) → replaced with fresh CockpitSession."""
    visitor = _visitor_with_state({SESSION_KEY: {"legacy": "shape"}})
    sess = get_session(visitor)
    assert isinstance(sess, CockpitSession)
    assert visitor._skill_state[SESSION_KEY] is sess


# ----------------------------------------------------------------------
# get_session_optional
# ----------------------------------------------------------------------


def test_get_session_optional_returns_none_for_missing_visitor() -> None:
    assert get_session_optional(None) is None


def test_get_session_optional_returns_none_when_state_missing() -> None:
    visitor = SimpleNamespace()
    assert get_session_optional(visitor) is None


def test_get_session_optional_returns_none_when_key_missing() -> None:
    visitor = _visitor_with_state()
    assert get_session_optional(visitor) is None


def test_get_session_optional_returns_existing_session() -> None:
    visitor = _visitor_with_state()
    sess = get_session(visitor)
    assert get_session_optional(visitor) is sess


# ----------------------------------------------------------------------
# clear_session
# ----------------------------------------------------------------------


def test_clear_session_resets_in_place_when_session_exists() -> None:
    visitor = _visitor_with_state()
    sess = get_session(visitor)
    sess.engine = "x"
    sess.finalized = True

    clear_session(visitor)

    # Same instance, all fields back to defaults.
    assert visitor._skill_state[SESSION_KEY] is sess
    assert sess.engine is None
    assert sess.finalized is False


def test_clear_session_no_op_when_session_absent() -> None:
    visitor = _visitor_with_state()
    # Must not raise even though no session has been created.
    clear_session(visitor)
    # Still no session created.
    assert SESSION_KEY not in visitor._skill_state
