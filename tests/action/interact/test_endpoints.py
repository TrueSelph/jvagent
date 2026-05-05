"""Tests for interact endpoint with production filtering."""

import logging
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interact.endpoints import _finalize_usage
from jvagent.action.interact.response_builder import (
    build_interact_response,
    build_interaction_payload,
)
from jvagent.core.config import get_environment_mode, is_production_mode


class TestEnvironmentMode:
    """Test environment mode detection functions."""

    def test_get_environment_mode_defaults_to_development(self):
        """Verify default mode is development when env var is unset."""
        original = os.environ.pop("JVSPATIAL_ENVIRONMENT", None)
        try:
            mode = get_environment_mode()
            assert mode == "development"
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original

    def test_get_environment_mode_production(self):
        """Verify production mode is detected."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "production"
            mode = get_environment_mode()
            assert mode == "production"
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    def test_get_environment_mode_case_insensitive(self):
        """Verify JVSPATIAL_ENVIRONMENT is case-insensitive."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            for value in ["PRODUCTION", "Production", "production", "PrOdUcTiOn"]:
                os.environ["JVSPATIAL_ENVIRONMENT"] = value
                assert is_production_mode() is True

            for value in ["DEVELOPMENT", "Development", "development", "DeVeLoPmEnT"]:
                os.environ["JVSPATIAL_ENVIRONMENT"] = value
                assert is_production_mode() is False
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    def test_is_production_mode(self):
        """Test is_production_mode helper."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "production"
            assert is_production_mode() is True

            os.environ["JVSPATIAL_ENVIRONMENT"] = "development"
            assert is_production_mode() is False
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    def test_invalid_env_value_defaults_to_development(self):
        """Verify unsupported values are treated as development."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "staging"
            assert get_environment_mode() == "development"
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)


class TestResponseBuilder:
    """Test response builder filtering logic."""

    def test_build_interaction_payload_production_mode(self):
        """Verify production mode returns minimal payload."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "production"

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
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    def test_build_interaction_payload_development_mode(self):
        """Verify development mode returns full payload."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "development"

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
                completed_tasks=[{"description": "Completed task"}],
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
                "completed_tasks": [{"description": "Completed task"}],
                "observability_metrics": [{"test": "metric"}],
                "usage": {},
                "streamed": False,
            }
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_production_mode(self):
        """Verify production mode excludes report."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "production"

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
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_development_mode(self):
        """Verify development mode includes report."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "development"

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
            assert "interaction" in response
            assert "completed_tasks" in response["interaction"]
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_includes_completed_tasks_for_window(self):
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "development"
            started = datetime.now(timezone.utc)
            finished = started + timedelta(seconds=5)

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
            interaction.conversation_id = "conv_123"
            interaction.started_at = started
            interaction.completed_at = finished

            in_window_completed = {
                "task_id": "t1",
                "description": "done in this interaction",
                "status": "completed",
                "updated_at": (started + timedelta(seconds=2)).isoformat(),
            }
            out_window_completed = {
                "task_id": "t2",
                "description": "done earlier",
                "status": "completed",
                "updated_at": (started - timedelta(seconds=2)).isoformat(),
            }
            active_task = {
                "task_id": "t3",
                "description": "still active",
                "status": "active",
                "updated_at": (started + timedelta(seconds=1)).isoformat(),
            }

            conversation = MagicMock()
            conversation.tasks = [
                in_window_completed,
                out_window_completed,
                active_task,
            ]
            conversation.get_tasks = MagicMock(
                side_effect=lambda status=None, owner_action=None: [
                    t
                    for t in conversation.tasks
                    if status is None or t.get("status") == status
                ]
            )

            with patch(
                "jvagent.memory.conversation.Conversation.get",
                AsyncMock(return_value=conversation),
            ):
                response = await build_interact_response(
                    user_id="usr_123",
                    session_id="sess_456",
                    interaction=interaction,
                    report=None,
                )

            assert response["interaction"]["active_tasks"] == [active_task]
            assert response["interaction"]["completed_tasks"] == [in_window_completed]
        finally:
            if original:
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)

    @pytest.mark.asyncio
    async def test_build_interact_response_no_report(self):
        """Verify response works without report."""
        original = os.environ.get("JVSPATIAL_ENVIRONMENT")
        try:
            os.environ["JVSPATIAL_ENVIRONMENT"] = "development"

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
                os.environ["JVSPATIAL_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVSPATIAL_ENVIRONMENT", None)


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
