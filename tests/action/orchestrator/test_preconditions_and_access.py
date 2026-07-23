"""Tests for precondition and AccessControl fail-open behavior (Wave 2 remediation)."""

from __future__ import annotations

import logging

import pytest

from jvagent.action.orchestrator.preconditions import (
    clear_preconditions,
    evaluate_precondition,
    precondition_registered,
    register_precondition,
)


@pytest.fixture(autouse=True)
def _clean_preconditions():
    """Reset precondition registry between tests."""
    clear_preconditions()
    yield
    clear_preconditions()


class TestPreconditions:
    """Precondition registry + fail-open semantics."""

    @pytest.mark.asyncio
    async def test_evaluate_unregistered_precondition_returns_true(self):
        """Unregistered precondition name → True (fail-open)."""
        result = await evaluate_precondition("nonexistent", visitor=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_evaluate_raising_predicate_returns_true(self):
        """Predicate that raises → True (fail-open)."""

        def _explode(_visitor):
            raise RuntimeError("boom")

        register_precondition("exploding", _explode)
        result = await evaluate_precondition("exploding", visitor=None)
        assert result is True

    def test_precondition_registered(self):
        """precondition_registered detects known names."""
        assert not precondition_registered("foo")
        register_precondition("foo", lambda _: True)
        assert precondition_registered("foo")

    @pytest.mark.asyncio
    async def test_skill_load_warns_on_unknown_precondition(self, caplog):
        """Skill declaring unregistered precondition → loud WARNING at load."""
        from jvagent.action.orchestrator.skills import _validate_preconditions

        requires = ({"when": "unknown_gate", "push": "prereq_skill", "seed_from": []},)
        with caplog.at_level(logging.WARNING):
            _validate_preconditions("test_skill", requires)

        assert any(
            "unknown_gate" in rec.message and "not registered" in rec.message
            for rec in caplog.records
        )


class TestAccessControl:
    """AccessControl fail-open / fail-closed."""

    @pytest.mark.asyncio
    async def test_missing_ac_allows_tool(self):
        """No AccessControlAction → allow (fail-open)."""
        from jvagent.action.orchestrator.access import is_tool_allowed

        # Mock agent with no AC.
        class FakeAgent:
            async def get_access_control_action(self):
                return None

        agent = FakeAgent()
        allowed = await is_tool_allowed(
            agent, label="test:tool", user_id="u1", channel="web"
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_ac_has_action_access_raises_denies_tool(self):
        """AC.has_action_access() raises → deny (fail-closed)."""
        from jvagent.action.orchestrator.access import is_tool_allowed

        class FakeAC:
            def policy_applies(self):
                return True

            async def has_action_access(self, user_id, action_label, channel):
                raise RuntimeError("AC wiring broken")

        class FakeAgent:
            async def get_access_control_action(self):
                return FakeAC()

        agent = FakeAgent()
        allowed = await is_tool_allowed(
            agent, label="test:tool", user_id="u1", channel="web"
        )
        assert allowed is False
