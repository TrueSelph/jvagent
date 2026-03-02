"""Tests for ResponseGatingInteractAction, including INTERVIEW bypass behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.gating.gating_result import POSTURE_SUPPRESS
from jvagent.action.gating.response_gating import ResponseGatingInteractAction

_GATING_MODULE = "jvagent.action.gating.response_gating"


@pytest.mark.asyncio
async def test_gating_bypassed_when_active_interview():
    """When active INTERVIEW task exists, bypass gating entirely (no LLM call), proceed as RESPOND."""
    gating = ResponseGatingInteractAction()

    visitor = MagicMock()
    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.utterance = "No"
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_active_tasks = MagicMock(
        return_value=[
            {
                "task_type": "INTERVIEW",
                "action_name": "SignupInterviewInteractAction",
                "status": "active",
            }
        ]
    )
    conversation.context = {}
    conversation.update_context = AsyncMock()
    conversation.save = AsyncMock()
    visitor.conversation = conversation

    with patch(
        f"{_GATING_MODULE}.ResponseGatingInteractAction.get_model_action",
        new_callable=AsyncMock,
    ) as mock_get_model:
        with patch(
            f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_respond",
            new_callable=AsyncMock,
        ) as mock_handle_respond:
            await gating.execute(visitor)

    mock_get_model.assert_not_called()
    mock_handle_respond.assert_called_once()
    assert interaction.response_posture == "RESPOND"


@pytest.mark.asyncio
async def test_suppress_not_overridden_when_no_active_interview():
    """When no active INTERVIEW task, SUPPRESS is applied normally."""
    gating = ResponseGatingInteractAction()

    visitor = MagicMock()
    visitor.data = {}  # No media; avoid media pass-through
    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.utterance = "ok"
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_active_tasks = MagicMock(return_value=[])
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.context = {}
    visitor.conversation = conversation

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "SUPPRESS", "reasoning": "Hanging ok"}'
    )

    with patch(
        f"{_GATING_MODULE}.ResponseGatingInteractAction.get_model_action",
        new_callable=AsyncMock,
        return_value=mock_model,
    ):
        with patch(
            f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_suppress",
            new_callable=AsyncMock,
        ) as mock_handle_suppress:
            with patch(
                f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_respond",
                new_callable=AsyncMock,
            ) as mock_handle_respond:
                await gating.execute(visitor)

    mock_handle_suppress.assert_called_once()
    mock_handle_respond.assert_not_called()
    assert interaction.response_posture == POSTURE_SUPPRESS


@pytest.mark.asyncio
async def test_pass_through_configurable_task_types():
    """Pass-through mode respects pass_through_task_types config."""
    gating = ResponseGatingInteractAction(
        pass_through_task_types=("INTERVIEW", "CUSTOM_FLOW")
    )

    visitor = MagicMock()
    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.utterance = "ok"
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_active_tasks = MagicMock(
        return_value=[
            {
                "task_type": "CUSTOM_FLOW",
                "action_name": "CustomFlowAction",
                "status": "active",
            }
        ]
    )
    conversation.context = {}
    conversation.update_context = AsyncMock()
    conversation.save = AsyncMock()
    visitor.conversation = conversation

    with patch(
        f"{_GATING_MODULE}.ResponseGatingInteractAction.get_model_action",
        new_callable=AsyncMock,
    ) as mock_get_model:
        with patch(
            f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_respond",
            new_callable=AsyncMock,
        ) as mock_handle_respond:
            await gating.execute(visitor)

    mock_get_model.assert_not_called()
    mock_handle_respond.assert_called_once()


@pytest.mark.asyncio
async def test_pass_through_disabled_when_empty():
    """When pass_through_task_types is empty, gating always runs."""
    gating = ResponseGatingInteractAction(pass_through_task_types=())

    visitor = MagicMock()
    visitor.data = {}  # No media; avoid media pass-through
    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.utterance = "No"
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_active_tasks = MagicMock(
        return_value=[
            {
                "task_type": "INTERVIEW",
                "action_name": "SignupInterviewInteractAction",
                "status": "active",
            }
        ]
    )
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.context = {}
    visitor.conversation = conversation

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "SUPPRESS", "reasoning": "Hanging ok"}'
    )

    with patch(
        f"{_GATING_MODULE}.ResponseGatingInteractAction.get_model_action",
        new_callable=AsyncMock,
        return_value=mock_model,
    ):
        with patch(
            f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_suppress",
            new_callable=AsyncMock,
        ) as mock_handle_suppress:
            with patch(
                f"{_GATING_MODULE}.ResponseGatingInteractAction._handle_respond",
                new_callable=AsyncMock,
            ) as mock_handle_respond:
                await gating.execute(visitor)

    mock_handle_suppress.assert_called_once()
    mock_handle_respond.assert_not_called()
