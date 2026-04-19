"""Tests for ActionResolver: resolution, caching, validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.skill.action_resolver import ActionResolver


class TestActionResolverResolve:
    """Test resolve() with caching."""

    @pytest.mark.asyncio
    async def test_resolve_returns_action(self):
        mock_action = MagicMock()
        mock_action.enabled = True
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        result = await resolver.resolve("GoogleCalendarAction")
        assert result is mock_action
        agent.get_action_by_type.assert_awaited_once_with("GoogleCalendarAction")

    @pytest.mark.asyncio
    async def test_resolve_returns_none_for_missing(self):
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=None)

        resolver = ActionResolver(agent)
        result = await resolver.resolve("NonexistentAction")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_caches_result(self):
        mock_action = MagicMock()
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        result1 = await resolver.resolve("GoogleCalendarAction")
        result2 = await resolver.resolve("GoogleCalendarAction")
        assert result1 is result2
        # Only called once despite two resolve calls
        agent.get_action_by_type.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolve_caches_none(self):
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=None)

        resolver = ActionResolver(agent)
        result1 = await resolver.resolve("MissingAction")
        result2 = await resolver.resolve("MissingAction")
        assert result1 is None
        assert result2 is None
        agent.get_action_by_type.assert_awaited_once()


class TestActionResolverRequire:
    """Test require() with strict validation."""

    @pytest.mark.asyncio
    async def test_require_returns_action_when_present(self):
        mock_action = MagicMock()
        mock_action.enabled = True
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        result = await resolver.require("GoogleCalendarAction")
        assert result is mock_action

    @pytest.mark.asyncio
    async def test_require_raises_when_missing(self):
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=None)

        resolver = ActionResolver(agent)
        with pytest.raises(ValueError, match="not found"):
            await resolver.require("MissingAction")

    @pytest.mark.asyncio
    async def test_require_raises_when_disabled(self):
        mock_action = MagicMock()
        mock_action.enabled = False
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        with pytest.raises(ValueError, match="disabled"):
            await resolver.require("DisabledAction")

    @pytest.mark.asyncio
    async def test_require_succeeds_when_enabled_true(self):
        mock_action = MagicMock()
        mock_action.enabled = True
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        result = await resolver.require("EnabledAction")
        assert result is mock_action

    @pytest.mark.asyncio
    async def test_require_succeeds_when_enabled_absent(self):
        """Action without 'enabled' attribute is treated as enabled."""
        mock_action = MagicMock(spec=[])
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        result = await resolver.require("NoEnabledAttrAction")
        assert result is mock_action


class TestActionResolverValidateRequirements:
    """Test batch validation."""

    @pytest.mark.asyncio
    async def test_validate_requirements_empty_list(self):
        resolver = ActionResolver(AsyncMock())
        errors = await resolver.validate_requirements([])
        assert errors == []

    @pytest.mark.asyncio
    async def test_validate_requirements_all_present(self):
        mock_action = MagicMock()
        mock_action.enabled = True
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=mock_action)

        resolver = ActionResolver(agent)
        errors = await resolver.validate_requirements(["GoogleCalendarAction"])
        assert errors == []

    @pytest.mark.asyncio
    async def test_validate_requirements_reports_missing(self):
        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(return_value=None)

        resolver = ActionResolver(agent)
        errors = await resolver.validate_requirements(["MissingAction"])
        assert len(errors) == 1
        assert "MissingAction" in errors[0]

    @pytest.mark.asyncio
    async def test_validate_requirements_reports_mixed(self):
        enabled_action = MagicMock()
        enabled_action.enabled = True

        async def side_effect(entity_type):
            if entity_type == "EnabledAction":
                return enabled_action
            disabled = MagicMock()
            disabled.enabled = False
            return disabled

        agent = AsyncMock()
        agent.get_action_by_type = AsyncMock(side_effect=side_effect)

        resolver = ActionResolver(agent)
        errors = await resolver.validate_requirements(
            ["EnabledAction", "DisabledAction"]
        )
        assert len(errors) == 1
        assert "DisabledAction" in errors[0]
