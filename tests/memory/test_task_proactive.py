"""Tests for ProactiveTaskSpec envelope."""

from __future__ import annotations

import pytest

from jvagent.memory.task_proactive import (
    PROACTIVE_TASK_TYPE,
    SPEC_VERSION,
    ProactiveTaskSpec,
)


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
