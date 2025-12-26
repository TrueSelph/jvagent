"""Tests for interact endpoint with production filtering."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interact.response_builder import (
    build_interact_response,
    build_interaction_payload,
)
from jvagent.utils.env import get_environment_mode, is_production_mode


class TestEnvironmentMode:
    """Test environment mode detection functions."""

    def test_get_environment_mode_defaults_to_development(self):
        """Verify default mode is development."""
        # Clear environment variable
        original = os.environ.pop("JVAGENT_ENVIRONMENT", None)
        try:
            mode = get_environment_mode()
            assert mode == "development"
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original

    def test_get_environment_mode_production(self):
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

    def test_get_environment_mode_case_insensitive(self):
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

    def test_is_production_mode(self):
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
            interaction.streamed = False

            payload = build_interaction_payload(interaction)

            # Development should include all fields
            assert payload == {
                "id": "int_123",
                "utterance": "Hello",
                "response": "Hi there!",
                "actions": ["Action1", "Action2"],
                "directives": [{"test": "directive"}],
                "parameters": [{"test": "parameter"}],
                "events": [{"test": "event"}],
                "observability_metrics": [{"test": "metric"}],
                "streamed": False,
            }
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_build_interact_response_production_mode(self):
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

            response = build_interact_response(
                user_id="usr_123",
                session_id="sess_456",
                interaction=interaction,
                report=report,
            )

            # Production should exclude report
            assert response["user_id"] == "usr_123"
            assert response["session_id"] == "sess_456"
            assert response["response"] == "Hi there!"
            assert "report" not in response
            assert "observability_metrics" not in response["interaction"]
        finally:
            if original:
                os.environ["JVAGENT_ENVIRONMENT"] = original
            else:
                os.environ.pop("JVAGENT_ENVIRONMENT", None)

    def test_build_interact_response_development_mode(self):
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

            response = build_interact_response(
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

    def test_build_interact_response_no_report(self):
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

            response = build_interact_response(
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

