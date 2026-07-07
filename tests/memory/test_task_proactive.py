"""Tests for ProactiveTaskSpec envelope."""

from __future__ import annotations

import pytest

from jvagent.memory.task_proactive import (
    PROACTIVE_TASK_TYPE,
    SPEC_VERSION,
    ProactiveTaskSpec,
    coerce_priority,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, 5),
        (5.9, 5),
        ("7", 7),
        ("high", 10),
        ("HIGH", 10),
        ("  Medium ", 5),
        ("low", 1),
        (None, 0),
        ("", 0),
        ("bogus", 0),
        (True, 0),
    ],
)
def test_coerce_priority(value, expected):
    """A model may pass priority='high' (or any non-int); coercion must never
    raise and must map named levels sensibly. Regression for the queue_task
    crash `invalid literal for int() with base 10: 'high'`."""
    assert coerce_priority(value) == expected


def test_from_data_coerces_string_priority():
    """A legacy/corrupt stored priority string must not blow up on read."""
    data = ProactiveTaskSpec(directive="x").to_data()
    data["priority"] = "high"
    spec = ProactiveTaskSpec.from_data(data)
    assert spec.priority == 10


def test_to_data_includes_spec_version():
    spec = ProactiveTaskSpec(directive="Check in with user")
    data = spec.to_data()
    assert data["spec_version"] == SPEC_VERSION
    assert data["directive"] == "Check in with user"
    assert PROACTIVE_TASK_TYPE == "PROACTIVE"


def test_from_data_round_trip():
    spec = ProactiveTaskSpec(
        directive="Follow up",
        context="user was busy",
        not_before="2026-06-01T10:00:00+00:00",
        trigger_on="keyword",
        trigger_keyword="busy",
        priority=2,
    )
    restored = ProactiveTaskSpec.from_data(spec.to_data())
    assert restored.directive == "Follow up"
    assert restored.trigger_on == "keyword"
    assert restored.priority == 2


def test_from_data_rejects_wrong_version():
    with pytest.raises(ValueError, match="unsupported"):
        ProactiveTaskSpec.from_data({"spec_version": 1, "directive": "x"})


def test_validate_requires_directive():
    with pytest.raises(ValueError):
        ProactiveTaskSpec(directive="").validate()
