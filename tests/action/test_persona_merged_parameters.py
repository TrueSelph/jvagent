"""Tests for merged parameters in persona prompt.

Verifies that when InteractActions add parameters to the interaction,
PersonaAction's composed prompt includes all of them (merged parameters).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.memory.interaction import Interaction
from jvagent.action.persona.persona_action import PersonaAction


class TestInteractionMergedParameters:
    """Test that Interaction aggregates parameters from multiple actions."""

    def test_get_unexecuted_parameters_returns_merged_from_multiple_actions(self):
        """Parameters from Interview and PersonaAction are all returned as unexecuted."""
        interaction = _make_interaction_like()
        interaction.parameters = []

        interview_params = [
            {"condition": "User is in interview flow", "response": "Guide through questions"},
        ]
        persona_params = [
            {"condition": "User asks about identity", "response": "Refer to yourself by name"},
        ]

        interaction.add_parameters(interview_params, "SignupInterviewInteractAction")
        interaction.add_parameters(persona_params, "PersonaAction")

        unexecuted = interaction.get_unexecuted_parameters()
        assert len(unexecuted) == 2

        conditions = [p.get("condition", "") for p in unexecuted]
        assert "User is in interview flow" in conditions
        assert "User asks about identity" in conditions


class TestPersonaComposePromptMergedParameters:
    """Test that PersonaAction._compose_prompt includes merged parameters."""

    @pytest.mark.asyncio
    async def test_compose_prompt_includes_all_applicable_parameters(self):
        """_compose_prompt includes both Interview and PersonaAction parameters in output."""
        interaction = MagicMock()
        interaction.response = None
        interaction.utterance = "Hello"
        interaction.interpretation = None
        interaction.channel = "default"

        applicable_directives = [{"content": "Ask the user a question", "executed": False}]
        applicable_parameters = [
            {"condition": "Interview condition", "response": "Interview response", "action_name": "InterviewAction"},
            {"condition": "Persona condition", "response": "Persona response", "action_name": "PersonaAction"},
        ]

        persona = PersonaAction()
        persona.remind_on_active_tasks = False

        from datetime import datetime, timezone

        mock_now = AsyncMock(return_value=datetime(2025, 2, 28, 12, 0, 0, tzinfo=timezone.utc))
        with patch(
            "jvagent.action.base.Action.now",
            mock_now,
        ):
            prompt = await persona._compose_prompt(
                interaction, applicable_directives, applicable_parameters
            )

        assert "Interview condition" in prompt
        assert "Interview response" in prompt
        assert "Persona condition" in prompt
        assert "Persona response" in prompt
        assert "### PARAMETERS" in prompt


def _make_interaction_like():
    """Create a minimal object with Interaction parameter methods."""
    obj = type("Obj", (), {"parameters": []})()
    obj.add_parameter = lambda p, a: Interaction.add_parameter(obj, p, a)
    obj.add_parameters = lambda plist, a: Interaction.add_parameters(obj, plist, a)
    obj.get_unexecuted_parameters = lambda: [
        p for p in obj.parameters if not p.get("executed", False)
    ]
    return obj
