"""Tests for InteractRouter posture classification and bypass."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.router.interact_router import InteractRouter
from jvagent.action.router.routing_result import POSTURE_SUPPRESS

_ROUTER_MODULE = "jvagent.action.router.interact_router"


@pytest.mark.asyncio
async def test_bypass_when_active_interview():
    """When active INTERVIEW task exists, bypass LLM, proceed as RESPOND."""
    router = InteractRouter()

    visitor = MagicMock()
    visitor.set_walk_path = AsyncMock()
    visitor.add_directive = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])
    visitor.data = {}

    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "No"
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.response = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_tasks = MagicMock(
        return_value=[
            {
                "task_type": "INTERVIEW",
                "owner_action": "SignupInterviewInteractAction",
                "status": "active",
            }
        ]
    )
    conversation.context = {}
    conversation.update_context = AsyncMock()
    conversation.save = AsyncMock()
    visitor.conversation = conversation

    mock_agent = MagicMock()
    mock_actions_manager = MagicMock()
    mock_agent.get_actions_manager = AsyncMock(return_value=mock_actions_manager)
    mock_actions_manager.get_all_actions = AsyncMock(return_value=[])

    with patch(
        f"{_ROUTER_MODULE}.InteractRouter.get_agent",
        new_callable=AsyncMock,
        return_value=mock_agent,
    ):
        with patch(
            f"{_ROUTER_MODULE}.InteractRouter.get_model_action",
            new_callable=AsyncMock,
        ) as mock_get_model:
            with patch(
                f"{_ROUTER_MODULE}.InteractRouter._handle_respond",
                new_callable=AsyncMock,
            ) as mock_handle_respond:
                with patch(
                    f"{_ROUTER_MODULE}.InteractRouter._publish_canned_response",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        f"{_ROUTER_MODULE}.InteractRouter._finalize_routing",
                        new_callable=AsyncMock,
                    ):
                        with patch(
                            f"{_ROUTER_MODULE}.Conversation.get",
                            new_callable=AsyncMock,
                            return_value=conversation,
                        ):
                            await router.execute(visitor)

    mock_get_model.assert_not_called()
    mock_handle_respond.assert_called_once()
    assert interaction.response_posture == "RESPOND"


@pytest.mark.asyncio
async def test_suppress_applied_when_no_active_interview():
    """When no active INTERVIEW task, SUPPRESS is applied when LLM returns it."""
    router = InteractRouter()

    visitor = MagicMock()
    visitor.data = {}
    visitor.set_walk_path = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])

    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "ok"
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_tasks = MagicMock(return_value=[])
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.get_active_tasks_for_context = MagicMock(return_value=[])
    conversation.context = {}
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
    mock_actions_manager.get_actions = AsyncMock(
        return_value=[mock_action],
    )

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "SUPPRESS", "interpretation": "Hanging ok; no response needed", '
        '"intent_type": "UNCLEAR", "actions": [], "confidence": 0.8}'
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
                f"{_ROUTER_MODULE}.InteractRouter._handle_suppress",
                new_callable=AsyncMock,
            ) as mock_handle_suppress:
                with patch(
                    f"{_ROUTER_MODULE}.InteractRouter._handle_respond",
                    new_callable=AsyncMock,
                ) as mock_handle_respond:
                    with patch(
                        f"{_ROUTER_MODULE}.Conversation.get",
                        new_callable=AsyncMock,
                        return_value=conversation,
                    ):
                        await router.execute(visitor)

    mock_handle_suppress.assert_called_once()
    mock_handle_respond.assert_not_called()
    assert interaction.response_posture == POSTURE_SUPPRESS


@pytest.mark.asyncio
async def test_bypass_respects_pass_through_task_types():
    """Bypass mode respects pass_through_task_types config."""
    router = InteractRouter(pass_through_task_types=("INTERVIEW", "CUSTOM_FLOW"))

    visitor = MagicMock()
    visitor.set_walk_path = AsyncMock()
    visitor.add_directive = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])
    visitor.data = {}

    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "ok"
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.response = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_tasks = MagicMock(
        return_value=[
            {
                "task_type": "CUSTOM_FLOW",
                "owner_action": "CustomFlowAction",
                "status": "active",
            }
        ]
    )
    conversation.context = {}
    conversation.update_context = AsyncMock()
    conversation.save = AsyncMock()
    visitor.conversation = conversation

    mock_agent = MagicMock()
    mock_actions_manager = MagicMock()
    mock_agent.get_actions_manager = AsyncMock(return_value=mock_actions_manager)
    mock_actions_manager.get_all_actions = AsyncMock(return_value=[])

    with patch(
        f"{_ROUTER_MODULE}.InteractRouter.get_agent",
        new_callable=AsyncMock,
        return_value=mock_agent,
    ):
        with patch(
            f"{_ROUTER_MODULE}.InteractRouter.get_model_action",
            new_callable=AsyncMock,
        ) as mock_get_model:
            with patch(
                f"{_ROUTER_MODULE}.InteractRouter._handle_respond",
                new_callable=AsyncMock,
            ) as mock_handle_respond:
                with patch(
                    f"{_ROUTER_MODULE}.InteractRouter._publish_canned_response",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        f"{_ROUTER_MODULE}.InteractRouter._finalize_routing",
                        new_callable=AsyncMock,
                    ):
                        with patch(
                            f"{_ROUTER_MODULE}.Conversation.get",
                            new_callable=AsyncMock,
                            return_value=conversation,
                        ):
                            await router.execute(visitor)

    mock_get_model.assert_not_called()
    mock_handle_respond.assert_called_once()


@pytest.mark.asyncio
async def test_bypass_disabled_when_empty_task_types():
    """When pass_through_task_types is empty, LLM always runs."""
    router = InteractRouter(pass_through_task_types=())

    visitor = MagicMock()
    visitor.data = {}
    visitor.set_walk_path = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])

    interaction = MagicMock()
    interaction.id = "int_123"
    interaction.conversation_id = "conv_123"
    interaction.utterance = "No"
    interaction.interpretation = None
    interaction.response_posture = None
    interaction.save = AsyncMock()
    visitor.interaction = interaction

    conversation = MagicMock()
    conversation.get_tasks = MagicMock(
        return_value=[
            {
                "task_type": "INTERVIEW",
                "owner_action": "SignupInterviewInteractAction",
                "status": "active",
            }
        ]
    )
    conversation.get_interaction_history = AsyncMock(return_value=[])
    conversation.get_active_tasks_for_context = MagicMock(return_value=[])
    conversation.context = {}
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
    mock_actions_manager.get_actions = AsyncMock(
        return_value=[mock_action],
    )

    mock_model = MagicMock()
    mock_model.generate = AsyncMock(
        return_value='{"posture": "SUPPRESS", "interpretation": "Hanging ok; no response needed", '
        '"intent_type": "UNCLEAR", "actions": [], "confidence": 0.8}'
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
                f"{_ROUTER_MODULE}.InteractRouter._handle_suppress",
                new_callable=AsyncMock,
            ) as mock_handle_suppress:
                with patch(
                    f"{_ROUTER_MODULE}.InteractRouter._handle_respond",
                    new_callable=AsyncMock,
                ) as mock_handle_respond:
                    with patch(
                        f"{_ROUTER_MODULE}.Conversation.get",
                        new_callable=AsyncMock,
                        return_value=conversation,
                    ):
                        await router.execute(visitor)

    mock_handle_suppress.assert_called_once()
    mock_handle_respond.assert_not_called()
