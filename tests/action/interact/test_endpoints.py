"""Tests for interact endpoint with production filtering."""

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interact.endpoints import _finalize_usage
from jvagent.action.interact.response_builder import (
    build_interact_response,
    build_interaction_payload,
)
from jvagent.utils.env import get_environment_mode, is_production_mode


@patch("jvagent.utils.env._get_environment_from_app_config", return_value=None)
class TestEnvironmentMode:
    """Test environment mode detection functions."""

    def test_get_environment_mode_defaults_to_development(self, mock_app_config):
        """Verify default mode is development when env and app config are unset."""
        original = os.environ.pop("JVAGENT_ENVIRONMENT", None)
        try:
            mode = get_environment_mode()
            assert mode == "development"
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original

    def test_get_environment_mode_production(self, mock_app_config):
        """Verify production mode is detected."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "production"
            mode = get_environment_mode()
            assert mode == "production"
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_get_environment_mode_case_insensitive(self, mock_app_config):
        """Verify JVAGENT_ENVIRONMENT is case-insensitive."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            for value in ["PRODUCTION", "Production", "production", "PrOdUcTiOn"]:
                os.environ["JVAGENT_ENVIRONMENT"] = value
                assert is_production_mode() is True

            for value in ["DEVELOPMENT", "Development", "development", "DeVeLoPmEnT"]:
                os.environ["JVAGENT_ENVIRONMENT"] = value
                assert is_production_mode() is False
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_is_production_mode(self, mock_app_config):
        """Test is_production_mode helper."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "production"
            assert is_production_mode() is True

            os.environ["JVAGENT_ENVIRONMENT"] = "development"
            assert is_production_mode() is False
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_get_environment_mode_from_app_config_production(self, mock_app_config):
        """Verify app config config.development.environment: production yields production mode."""
        original = os.environ.pop("JVAGENT_ENVIRONMENT", None)
        try:
            with patch(
                "jvagent.utils.env._get_environment_from_app_config",
                return_value="production",
            ):
                mode = get_environment_mode()
                assert mode == "production"
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original

    @patch(
        "jvagent.utils.env._get_environment_from_app_config", return_value="development"
    )
    def test_get_environment_mode_from_app_config_development(
        self, mock_app_config_development, mock_app_config
    ):
        """Verify app config config.development.environment: development yields development mode."""
        original = os.environ.pop("JVAGENT_ENVIRONMENT", None)
        try:
            mode = get_environment_mode()
            assert mode == "development"
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original

    @patch(
        "jvagent.utils.env._get_environment_from_app_config", return_value="production"
    )
    def test_env_var_overrides_app_config(
        self, mock_app_config_production, mock_app_config
    ):
        """Verify JVAGENT_ENVIRONMENT overrides app config when both are set."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "development"
            mode = get_environment_mode()
            assert mode == "development"
            mock_app_config.assert_not_called()
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)


class TestResponseBuilder:
    """Test response builder filtering logic."""

    def test_build_interaction_payload_production_mode(self):
        """Verify production mode returns minimal payload."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "production"

            # Create mock interaction
            interaction = MagicMock()
            interaction.id = "int_123"
            interaction.utterance = "Hello"
            interaction.response = "Hi there!"
            interaction.actions = ["Action1", "Action2"]
            interaction.directives = [{"test": "directive"}]
            interaction.parameters = [{"test": "parameter"}]
            interaction.events = [{"test": "event"}]
            interaction.observability_metrics = [{"test": "metric"}]
            interaction.streamed = False

            payload = build_interaction_payload(interaction)

            # Production should only include essential fields
            assert payload == {
                "id": "int_123",
                "utterance": "Hello",
                "response": "Hi there!",
            }
            assert "actions" not in payload
            assert "directives" not in payload
            assert "parameters" not in payload
            assert "events" not in payload
            assert "observability_metrics" not in payload
            assert "streamed" not in payload
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_build_interaction_payload_development_mode(self):
        """Verify development mode returns full payload."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "development"

            # Create mock interaction
            interaction = MagicMock()
            interaction.id = "int_123"
            interaction.utterance = "Hello"
            interaction.response = "Hi there!"
            interaction.actions = ["Action1", "Action2"]
            interaction.directives = [{"test": "directive"}]
            interaction.parameters = [{"test": "parameter"}]
            interaction.events = [{"test": "event"}]
            interaction.observability_metrics = [{"test": "metric"}]
            interaction.usage = {}
            interaction.streamed = False

            payload = build_interaction_payload(
                interaction,
                active_tasks=[{"description": "Guide user to complete Signup"}],
            )

            # Development should include all fields including usage
            assert payload == {
                "id": "int_123",
                "utterance": "Hello",
                "response": "Hi there!",
                "actions": ["Action1", "Action2"],
                "directives": [{"test": "directive"}],
                "parameters": [{"test": "parameter"}],
                "events": [{"test": "event"}],
                "active_tasks": [{"description": "Guide user to complete Signup"}],
                "observability_metrics": [{"test": "metric"}],
                "usage": {},
                "streamed": False,
            }
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_production_mode(self):
        """Verify production mode excludes report."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "production"

            # Create mock interaction
            interaction = MagicMock()
            interaction.id = "int_123"
            interaction.utterance = "Hello"
            interaction.response = "Hi there!"
            interaction.actions = []
            interaction.directives = []
            interaction.parameters = []
            interaction.events = []
            interaction.observability_metrics = []
            interaction.streamed = False

            report = [{"test": "report"}]

            response = await build_interact_response(
                user_id="usr_123",
                session_id="sess_456",
                interaction=interaction,
                report=report,
            )

            # Production should exclude report and interaction
            assert response["user_id"] == "usr_123"
            assert response["session_id"] == "sess_456"
            assert response["response"] == "Hi there!"
            assert "report" not in response
            assert "interaction" not in response
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_development_mode(self):
        """Verify development mode includes report."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "development"

            # Create mock interaction
            interaction = MagicMock()
            interaction.id = "int_123"
            interaction.utterance = "Hello"
            interaction.response = "Hi there!"
            interaction.actions = []
            interaction.directives = []
            interaction.parameters = []
            interaction.events = []
            interaction.observability_metrics = []
            interaction.streamed = False

            report = [{"test": "report"}]

            response = await build_interact_response(
                user_id="usr_123",
                session_id="sess_456",
                interaction=interaction,
                report=report,
            )

            # Development should include report
            assert response["user_id"] == "usr_123"
            assert response["session_id"] == "sess_456"
            assert response["response"] == "Hi there!"
            assert "report" in response
            assert response["report"] == [{"test": "report"}]
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_no_report(self):
        """Verify response works without report."""
        original = os.environ.get("JVAGENT_ENVIRONMENT")
        try:
            os.environ["JVAGENT_ENVIRONMENT"] = "development"

            # Create mock interaction
            interaction = MagicMock()
            interaction.id = "int_123"
            interaction.utterance = "Hello"
            interaction.response = "Hi there!"
            interaction.actions = []
            interaction.directives = []
            interaction.parameters = []
            interaction.events = []
            interaction.observability_metrics = []
            interaction.streamed = False

            response = await build_interact_response(
                user_id="usr_123",
                session_id="sess_456",
                interaction=interaction,
                report=None,
            )

            # Should not include report when None
            assert "report" not in response
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)


class TestFinalizeUsage:
    """Tests for _finalize_usage."""

    @pytest.mark.asyncio
    async def test_finalize_usage_get_user_none_completes_without_error(self):
        """When get_user returns None, _finalize_usage completes without raising."""
        interaction = MagicMock()
        interaction.compute_usage = MagicMock()
        interaction.save = AsyncMock()
        interaction.usage = {"total_tokens": 100}
        interaction.get_user = AsyncMock(return_value=None)
        interaction.id = "int_123"
        interaction.user_id = "usr_456"

        await _finalize_usage(interaction)

        interaction.compute_usage.assert_called_once()
        interaction.save.assert_called_once()
        interaction.get_user.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_usage_add_usage_fails_logs_warning(self, caplog):
        """When add_usage_from_interaction raises, _finalize_usage logs warning."""
        interaction = MagicMock()
        interaction.compute_usage = MagicMock()
        interaction.save = AsyncMock()
        interaction.usage = {"total_tokens": 100}
        user = MagicMock()
        user.add_usage_from_interaction = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        interaction.get_user = AsyncMock(return_value=user)
        interaction.id = "int_123"
        interaction.user_id = "usr_456"

        with caplog.at_level(logging.WARNING):
            await _finalize_usage(interaction)

        assert any(
            "Failed to update user usage stats" in rec.message for rec in caplog.records
        )
        assert any("int_123" in rec.message for rec in caplog.records)
        assert any("usr_456" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_finalize_usage_integration_updates_user(self, test_db):
        """_finalize_usage computes usage and updates user stats."""
        import uuid

        from jvagent.memory.user import User

        user = await User.create(user_id=f"test-{uuid.uuid4().hex[:12]}")
        conv = await user.create_conversation()
        try:
            interaction = await conv.create_interaction("Hello")
            interaction.observability_metrics = [
                {
                    "event_type": "model_call",
                    "data": {
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                            "total_tokens": 150,
                        },
                        "duration": 0.3,
                        "model": "gpt-4o-mini",
                        "provider": "openai",
                    },
                },
            ]
            await interaction.save()

            await _finalize_usage(interaction)

            await interaction.save()
            interaction = await type(interaction).get(interaction.id)
            assert interaction.usage["total_tokens"] == 150
            assert interaction.usage["model_call_count"] == 1

            user = await User.get(user.id)
            stats = user.get_usage_statistics()
            assert stats["total_tokens"] == 150
            assert stats["interaction_count"] == 1
        finally:
            await user.delete(cascade=True)
