"""Tests for InteractRouter fragment accumulation (DEFER -> buffer -> RESPOND)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.router.interact_router import BUFFER_KEY, InteractRouter

_ROUTER_MODULE = "jvagent.action.router.interact_router"


@pytest.mark.asyncio
async def test_defer_appends_to_buffer():
    """When LLM returns DEFER, utterance is appended to conversation buffer."""
    router = InteractRouter(enable_accumulation=True)

    visitor = MagicMock()
    visitor.data = {}
    visitor.set_walk_path = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])

    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "Actually..."
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    # Use visitor.conversation so buffer updates go to the flushed instance
    conversation = MagicMock()
    conversation.context = {}
    conversation.get_active_tasks = MagicMock(return_value=[])
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.get_active_tasks_for_context = MagicMock(return_value=[])
    conversation.update_context = AsyncMock()
    visitor.conversation = conversation

    mock_agent = MagicMock()
    mock_actions_manager = MagicMock()
    mock_agent.get_actions_manager = AsyncMock(return_value=mock_actions_manager)
    mock_actions_manager.get_all_actions = AsyncMock(return_value=[])
    mock_action = MagicMock(
        get_class_name=lambda: "PageIndexRetrievalInteractAction",
        anchors=["search"],
        description="",
        id="a2",
        always_execute=False,
    )
    mock_actions_manager.get_actions = AsyncMock(return_value=[mock_action])

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "DEFER", "interpretation": "Fragmentary; lacks context", '
        '"intent_type": "UNCLEAR", "actions": [], "confidence": 0.7}'
    )

    with patch(
        f"{_ROUTER_MODULE}.InteractRouter.get_agent",
        new_callable=AsyncMock,
        return_value=mock_agent,
    ):
        with patch(
            f"{_ROUTER_MODULE}.InteractRouter.get_model_action",
            new_callable=AsyncMock,
            return_value=mock_model,
        ):
            await router.execute(visitor)

    # Buffer should be updated with the deferred utterance
    conversation.update_context.assert_called_once()
    call_args = conversation.update_context.call_args[0][0]
    assert BUFFER_KEY in call_args
    buffer = call_args[BUFFER_KEY]
    assert len(buffer) == 1
    assert buffer[0].get("utterance") == "Actually..."
    assert buffer[0].get("interaction_id") == "int_123"
    visitor.set_walk_path.assert_called_once_with([])


@pytest.mark.asyncio
async def test_respond_consumes_buffer_and_injects_directive():
    """When RESPOND with prior buffer, directive is injected and buffer cleared."""
    router = InteractRouter(enable_accumulation=True)

    visitor = MagicMock()
    visitor.data = {}
    visitor.set_walk_path = AsyncMock()
    visitor.add_directive = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])

    interaction = MagicMock()
    interaction.id = "int_456"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "I meant the blue one"
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    # Pre-populate buffer from prior DEFER
    conversation = MagicMock()
    conversation.context = {
        BUFFER_KEY: [
            {
                "utterance": "Actually...",
                "interaction_id": "int_123",
                "timestamp": "2024-01-01T00:00:00",
            },
        ]
    }
    conversation.get_active_tasks = MagicMock(return_value=[])
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.get_active_tasks_for_context = MagicMock(return_value=[])
    conversation.update_context = AsyncMock()
    visitor.conversation = conversation

    mock_agent = MagicMock()
    mock_actions_manager = MagicMock()
    mock_agent.get_actions_manager = AsyncMock(return_value=mock_actions_manager)
    mock_actions_manager.get_all_actions = AsyncMock(return_value=[])
    mock_action = MagicMock(
        get_class_name=lambda: "PageIndexRetrievalInteractAction",
        anchors=["search"],
        description="",
        id="a2",
        always_execute=False,
    )
    mock_actions_manager.get_actions = AsyncMock(return_value=[mock_action])

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "RESPOND", "interpretation": "User clarified; wants blue option", '
        '"intent_type": "INFORMATIONAL", "actions": ["PageIndexRetrievalInteractAction"], '
        '"confidence": 0.9, "canned_response": "Let me check"}'
    )

    with patch(
        f"{_ROUTER_MODULE}.InteractRouter.get_agent",
        new_callable=AsyncMock,
        return_value=mock_agent,
    ):
        with patch(
            f"{_ROUTER_MODULE}.InteractRouter.get_model_action",
            new_callable=AsyncMock,
            return_value=mock_model,
        ):
            with patch(
                f"{_ROUTER_MODULE}.InteractRouter._publish_canned_response",
                new_callable=AsyncMock,
            ):
                with patch(
                    f"{_ROUTER_MODULE}.InteractRouter._finalize_routing",
                    new_callable=AsyncMock,
                ):
                    await router.execute(visitor)

    # Directive should be injected with prior fragments
    visitor.add_directive.assert_called_once()
    directive = visitor.add_directive.call_args[0][0]
    assert "Actually..." in directive
    assert "fragmented thought" in directive or "Prior fragments" in directive

    # Buffer should be cleared
    conversation.update_context.assert_called_with({BUFFER_KEY: []})
