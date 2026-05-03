"""Unit tests for AgentInteractAction: smoke, conversational path, router helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction
from jvagent.action.router.routing_result import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    RoutingResult,
)

_ACTION_MODULE = "jvagent.action.agent_interact.agent_interact_action"
_ROUTER_MODULE = "jvagent.action.agent_interact.skill_handler.skill_router"


def _make_visitor(**overrides):
    visitor = MagicMock()
    visitor.set_walk_path = AsyncMock()
    visitor.add_directive = AsyncMock()
    visitor.curate_walk_path = AsyncMock(return_value=[])
    visitor.interaction = MagicMock()
    visitor.interaction.id = "int_1"
    visitor.interaction.conversation_id = "conv_1"
    visitor.interaction.utterance = "Hello"
    visitor.interaction.interpretation = None
    visitor.interaction.response_posture = None
    visitor.interaction.response = None
    visitor.interaction.canned_response = None
    visitor.interaction.save = AsyncMock()
    visitor.interaction.set_to_executed = MagicMock()
    visitor.interaction.record_action_execution = MagicMock()
    visitor.interaction.unrecord_action_execution = MagicMock()
    visitor.conversation = MagicMock()
    visitor.conversation.id = "conv_1"
    visitor.conversation.context = {}
    visitor.conversation.update_context = AsyncMock()
    visitor.conversation.get_active_tasks = MagicMock(return_value=[])
    visitor.conversation.get_active_tasks_for_context = MagicMock(return_value=[])
    visitor.conversation.get_interaction_history = AsyncMock(return_value=[])
    visitor.data = {}
    visitor.unrecord_action_execution = AsyncMock()
    visitor._agent = MagicMock()
    visitor._agent.namespace = "test"
    visitor._agent.name = "test_agent"
    visitor._agent.get_actions_manager = AsyncMock(return_value=MagicMock())
    visitor.session_id = "sess_1"
    visitor.channel = "test"
    visitor.stream = False
    visitor.user_id = "user_1"
    visitor.tasks = MagicMock()
    visitor.response_bus = MagicMock()
    visitor.response_bus.commit_pending_adhoc = AsyncMock()
    visitor.response_bus.commit_pending_thoughts = AsyncMock()
    visitor._skill_state = {}
    for k, v in overrides.items():
        setattr(visitor, k, v)
    return visitor


# ---------------------------------------------------------------------------
# Smoke test: action can be instantiated
# ---------------------------------------------------------------------------


class TestAgentInteractActionSmoke:
    def test_action_class_imports(self):
        assert AgentInteractAction.__name__ == "AgentInteractAction"

    def test_action_default_weight(self):
        action = AgentInteractAction()
        assert action.weight == -200

    def test_action_is_interact_action(self):
        from jvagent.action.interact.base import InteractAction

        action = AgentInteractAction()
        assert isinstance(action, InteractAction)

    def test_native_conv_enabled_by_default(self):
        action = AgentInteractAction()
        assert action.native_conv_enabled is True

    def test_native_conv_default_model(self):
        action = AgentInteractAction()
        assert action.native_conv_model == "gpt-4o-mini"

    def test_router_model_default(self):
        action = AgentInteractAction()
        assert action.router_model == "gpt-4o-mini"

    def test_enable_canned_response_by_default(self):
        action = AgentInteractAction()
        assert action.enable_canned_response is True

    @pytest.mark.asyncio
    async def test_healthcheck_valid_config(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "AnthropicLanguageModelAction")
        object.__setattr__(action, "max_iterations", 25)
        result = await action.healthcheck()
        assert result is True

    @pytest.mark.asyncio
    async def test_healthcheck_missing_model_action_type(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "")
        object.__setattr__(action, "max_iterations", 25)
        result = await action.healthcheck()
        assert result is False

    @pytest.mark.asyncio
    async def test_healthcheck_zero_iterations(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "AnthropicLanguageModelAction")
        object.__setattr__(action, "max_iterations", 0)
        result = await action.healthcheck()
        assert result is False


# ---------------------------------------------------------------------------
# Routing: posture (SUPPRESS / DEFER / RESPOND) via SkillRouter
# ---------------------------------------------------------------------------


class TestAgentInteractRouting:
    @pytest.mark.asyncio
    async def test_suppress_returns_early(self):
        """When posture=SUPPRESS, execute() returns immediately (router handles walk path)."""
        action = AgentInteractAction()
        visitor = _make_visitor()

        result = RoutingResult(posture=POSTURE_SUPPRESS, interpretation="Bye")
        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_SUPPRESS, result),
        ):
            await action.execute(visitor)

        # execute() should return early, NOT enter any execution path

    @pytest.mark.asyncio
    async def test_defer_returns_early(self):
        action = AgentInteractAction()
        visitor = _make_visitor()

        result = RoutingResult(posture=POSTURE_DEFER)
        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_DEFER, result),
        ):
            await action.execute(visitor)

    @pytest.mark.asyncio
    async def test_conversational_intent_triggers_native_conv(self):
        action = AgentInteractAction()
        object.__setattr__(action, "native_conv_enabled", True)
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ):
            with patch(
                f"{_ACTION_MODULE}.NativeConversation.respond",
                new_callable=AsyncMock,
            ) as mock_conv:
                await action.execute(visitor)

        mock_conv.assert_called_once_with(visitor)

    @pytest.mark.asyncio
    async def test_no_actions_triggers_native_conv(self):
        action = AgentInteractAction()
        object.__setattr__(action, "native_conv_enabled", True)
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="DIRECTIVE",
            actions=[],
            confidence=1.0,
        )

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ):
            with patch(
                f"{_ACTION_MODULE}.NativeConversation.respond",
                new_callable=AsyncMock,
            ) as mock_conv:
                await action.execute(visitor)

        mock_conv.assert_called_once_with(visitor)

    @pytest.mark.asyncio
    async def test_native_conv_disabled_goes_to_skill_loop(self):
        action = AgentInteractAction()
        object.__setattr__(action, "native_conv_enabled", False)
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ):
            with patch(
                f"{_ACTION_MODULE}.AgentInteractAction._phase_execute_skill_loop",
                new_callable=AsyncMock,
            ) as mock_skill:
                await action.execute(visitor)

        mock_skill.assert_called_once()

    @pytest.mark.asyncio
    async def test_skill_intent_triggers_skill_loop(self):
        action = AgentInteractAction()
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="DIRECTIVE",
            actions=["outlook_mail"],
            confidence=0.9,
        )

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ):
            with patch(
                f"{_ACTION_MODULE}.AgentInteractAction._phase_execute_skill_loop",
                new_callable=AsyncMock,
            ) as mock_skill:
                await action.execute(visitor)

        mock_skill.assert_called_once_with(visitor, result)

    @pytest.mark.asyncio
    async def test_null_routing_result_defaults_to_conv(self):
        action = AgentInteractAction()
        object.__setattr__(action, "native_conv_enabled", True)
        visitor = _make_visitor()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, None),
        ):
            with patch(
                f"{_ACTION_MODULE}.NativeConversation.respond",
                new_callable=AsyncMock,
            ) as mock_conv:
                await action.execute(visitor)

        mock_conv.assert_called_once_with(visitor)

    @pytest.mark.asyncio
    async def test_no_interaction_unrecords_and_returns(self):
        action = AgentInteractAction()
        visitor = _make_visitor()
        visitor.interaction = None

        await action.execute(visitor)
        visitor.unrecord_action_execution.assert_called_once()


# ---------------------------------------------------------------------------
# Skill name resolution (from descriptors)
# ---------------------------------------------------------------------------


class TestSkillNameResolution:
    def test_valid_skill_names_pass_through(self):
        from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter

        descriptors = {
            "outlook_mail": {"description": "Send/list emails"},
            "google_drive": {"description": "Upload/list files"},
        }
        result = SkillRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "google_drive"], descriptors
        )
        assert result == ["outlook_mail", "google_drive"]

    def test_drops_invalid_skill_names(self):
        from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter

        descriptors = {"outlook_mail": {"description": "Send emails"}}
        result = SkillRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "made_up_skill", "gibberish"], descriptors
        )
        assert result == ["outlook_mail"]

    def test_deduplication(self):
        from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        result = SkillRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "outlook_mail"], descriptors
        )
        assert result == ["outlook_mail"]

    def test_empty_input(self):
        from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        assert SkillRouter._resolve_skill_names_to_keys([], descriptors) == []

    def test_empty_strings_skipped(self):
        from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        result = SkillRouter._resolve_skill_names_to_keys(
            ["", "outlook_mail", " "], descriptors
        )
        assert result == ["outlook_mail"]


# ---------------------------------------------------------------------------
# Multi-provider model resolution
# ---------------------------------------------------------------------------


class TestAgentInteractModelResolution:
    def test_language_model_action_type_per_purpose(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "OllamaLanguageModelAction")
        object.__setattr__(
            action, "router_model_action_type", "OpenAILanguageModelAction"
        )
        object.__setattr__(
            action, "native_conv_model_action_type", "OpenAILanguageModelAction"
        )

        assert action._language_model_action_type_for_purpose("skill") == (
            "OllamaLanguageModelAction"
        )
        assert action._language_model_action_type_for_purpose("router") == (
            "OpenAILanguageModelAction"
        )
        assert action._language_model_action_type_for_purpose("native") == (
            "OpenAILanguageModelAction"
        )

    def test_router_falls_back_to_skill_type(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "AnthropicLanguageModelAction")
        assert action._language_model_action_type_for_purpose("router") == (
            "AnthropicLanguageModelAction"
        )
        assert action._language_model_action_type_for_purpose("native") == (
            "AnthropicLanguageModelAction"
        )


# ---------------------------------------------------------------------------
# NativeConversation unit tests
# ---------------------------------------------------------------------------


class TestNativeConversation:
    def test_effective_model_ollama_replaces_default_mini_with_primary(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        conv = NativeConversation(action)
        ma = SimpleNamespace(provider="ollama", model="deepseek-v4-flash:cloud")
        assert conv._effective_model(ma) == "deepseek-v4-flash:cloud"

    def test_effective_model_ollama_respects_explicit_native_model(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        object.__setattr__(action, "native_conv_model", "llama3.2")
        conv = NativeConversation(action)
        ma = SimpleNamespace(provider="ollama", model="deepseek-v4-flash:cloud")
        assert conv._effective_model(ma) == "llama3.2"

    def test_effective_model_openai_keeps_default_mini(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        conv = NativeConversation(action)
        ma = SimpleNamespace(provider="openai", model="gpt-4o")
        assert conv._effective_model(ma) == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_native_conv_integration(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        model = MagicMock()
        model.generate = AsyncMock(return_value="Hi there!")
        model.provider = "openai"
        model.model = "gpt-4o-mini"

        conv = NativeConversation(action)
        visitor = _make_visitor()
        visitor.conversation.get_interaction_history = AsyncMock(return_value=[])

        with patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await conv.respond(visitor)

        mock_publish.assert_called_once_with(
            visitor, content="Hi there!", streaming_complete=True
        )

    @pytest.mark.asyncio
    async def test_native_conv_caches_model(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        model = MagicMock()
        model.generate = AsyncMock(return_value="Hello!")
        model.provider = "openai"
        model.model = "gpt-4o-mini"

        conv = NativeConversation(action)
        visitor = _make_visitor()
        visitor.conversation.get_interaction_history = AsyncMock(return_value=[])

        with patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ) as mock_get_model, patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ):
            await conv.respond(visitor)
            await conv.respond(visitor)

        assert mock_get_model.call_count == 1

    @pytest.mark.asyncio
    async def test_native_conv_no_conversation_returns_early(self):
        from jvagent.action.agent_interact.converse import NativeConversation

        action = AgentInteractAction()
        conv = NativeConversation(action)
        visitor = _make_visitor()
        visitor.conversation = None

        with patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await conv.respond(visitor)
        mock_publish.assert_not_called()


# ---------------------------------------------------------------------------
# Persona directive formatting
# ---------------------------------------------------------------------------


class TestPersonaDirective:
    def test_format_includes_utterance_and_content(self):
        d = AgentInteractAction._format_persona_directive(
            "What skills do you have?", "A, B, C"
        )
        assert "What skills do you have?" in d
        assert "A, B, C" in d

    def test_format_with_none_utterance(self):
        d = AgentInteractAction._format_persona_directive(None, "Result")
        assert "(no utterance)" in d
        assert "Result" in d


# ---------------------------------------------------------------------------
# Response mode normalization
# ---------------------------------------------------------------------------


class TestResponseModeNormalization:
    def test_normalize_respond(self):
        action = AgentInteractAction()
        object.__setattr__(action, "response_mode", "publish")
        assert action._normalize_effective_response_mode("respond") == "respond"

    def test_normalize_publish(self):
        action = AgentInteractAction()
        assert action._normalize_effective_response_mode("publish") == "publish"

    def test_normalize_invalid_falls_to_publish(self):
        action = AgentInteractAction()
        object.__setattr__(action, "response_mode", "publish")
        assert action._normalize_effective_response_mode("banana") == "publish"

    def test_normalize_inherits_from_response_mode(self):
        action = AgentInteractAction()
        object.__setattr__(action, "response_mode", "respond")
        assert action._normalize_effective_response_mode("banana") == "respond"


# ---------------------------------------------------------------------------
# Always-active skill resolution
# ---------------------------------------------------------------------------


class TestAlwaysActiveResolution:
    @pytest.mark.asyncio
    async def test_always_active_empty_when_no_catalog(self):
        action = AgentInteractAction()
        mock_agent = MagicMock()
        mock_agent.namespace = "test"
        mock_agent.name = "test_agent"
        mock_agent.get_actions_manager = AsyncMock(return_value=MagicMock())
        conversation = MagicMock()
        with patch(
            "jvagent.action.skill.skill_catalog.SkillCatalog.discover",
            new_callable=AsyncMock,
            return_value=MagicMock(skills={}),
        ):
            result = await action._get_always_active_skills(mock_agent, conversation)
            assert result == []
