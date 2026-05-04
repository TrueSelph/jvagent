"""Unit tests for AgentInteractAction: smoke, conversational path, router helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.agent_interact.agent_interact_action import (
    AgentInteractAction,
    _skill_loop_output_is_deliverable,
)
from jvagent.action.agent_interact.skill.converse_delivery import (
    format_conversational_directive_for_persona,
)
from jvagent.action.router.routing_result import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    RoutingResult,
)

_ACTION_MODULE = "jvagent.action.agent_interact.agent_interact_action"
_ROUTER_MODULE = "jvagent.action.agent_interact.router.service"


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


def _make_persona_mock(*, slim_reply: str = "Slim LM reply."):
    """Stub Persona with ``respond_slim`` async mock."""
    persona = MagicMock()
    persona.enabled = True
    persona.persona_description = "Test persona description body."
    persona.persona_name = "TestAgent"
    persona.model = "gpt-4o-mini"
    persona.model_temperature = 0.2
    persona.model_max_tokens = 2048
    persona.respond_slim = AsyncMock(return_value=slim_reply)
    return persona


def _patch_require_persona(persona):
    return patch(
        f"{_ACTION_MODULE}.AgentInteractAction._require_persona_for_interact",
        new_callable=AsyncMock,
        return_value=persona,
    )


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

    def test_converse_enabled_by_default(self):
        action = AgentInteractAction()
        assert action.converse_enabled is True

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

    @pytest.mark.asyncio
    async def test_healthcheck_fails_when_agent_but_no_persona(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "AnthropicLanguageModelAction")
        object.__setattr__(action, "max_iterations", 10)
        with patch.object(
            AgentInteractAction,
            "get_agent",
            new=AsyncMock(return_value=MagicMock(id="ag1")),
        ), patch.object(
            AgentInteractAction,
            "get_action",
            new=AsyncMock(return_value=None),
        ):
            assert await action.healthcheck() is False

    @pytest.mark.asyncio
    async def test_execute_raises_when_persona_missing(self):
        action = AgentInteractAction()
        visitor = _make_visitor()
        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )
        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch.object(
            AgentInteractAction,
            "_require_persona_for_interact",
            AsyncMock(side_effect=RuntimeError("no persona")),
        ):
            with pytest.raises(RuntimeError, match="no persona"):
                await action.execute(visitor)
        visitor.unrecord_action_execution.assert_called_once()


# ---------------------------------------------------------------------------
# Routing: posture (SUPPRESS / DEFER / RESPOND) via AgentInteractRouter
# ---------------------------------------------------------------------------


class TestAgentInteractRouting:
    @pytest.mark.asyncio
    async def test_suppress_returns_early(self):
        """When posture=SUPPRESS, execute() returns immediately (router handles walk path)."""
        action = AgentInteractAction()
        visitor = _make_visitor()

        result = RoutingResult(posture=POSTURE_SUPPRESS, interpretation="Bye")
        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
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
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_DEFER, result),
        ):
            await action.execute(visitor)

    @pytest.mark.asyncio
    async def test_conversational_intent_triggers_converse_path(self):
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        object.__setattr__(action, "response_mode", "publish")
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )

        persona = _make_persona_mock(slim_reply="Hi from slim path")

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona):
            await action.execute(visitor)

        persona.respond_slim.assert_called_once()
        _args, kwargs = persona.respond_slim.call_args
        assert kwargs.get("prompt") == "Hello"
        assert kwargs.get("history") == []

    @pytest.mark.asyncio
    async def test_directive_intent_goes_to_skill_loop_even_with_empty_actions(self):
        """DIRECTIVE with empty actions is forced to skill loop (Fix 1)."""
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        object.__setattr__(action, "response_mode", "publish")
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="DIRECTIVE",
            actions=[],
            confidence=1.0,
        )

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona), patch(
            f"{_ACTION_MODULE}.AgentInteractAction._phase_execute_skill_loop",
            new_callable=AsyncMock,
        ) as mock_skill:
            await action.execute(visitor)

        # With Fix 1, DIRECTIVE always enters the skill loop—never persona
        mock_skill.assert_called_once()
        persona.respond_slim.assert_not_called()

    @pytest.mark.asyncio
    async def test_informational_intent_goes_to_skill_loop_even_with_empty_actions(
        self,
    ):
        """INFORMATIONAL with empty actions is forced to skill loop (Fix 1)."""
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        object.__setattr__(action, "response_mode", "publish")
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="INFORMATIONAL",
            actions=[],
            confidence=1.0,
        )

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona), patch(
            f"{_ACTION_MODULE}.AgentInteractAction._phase_execute_skill_loop",
            new_callable=AsyncMock,
        ) as mock_skill:
            await action.execute(visitor)

        mock_skill.assert_called_once()
        persona.respond_slim.assert_not_called()

    @pytest.mark.asyncio
    async def test_converse_disabled_goes_to_skill_loop(self):
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", False)
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona), patch(
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

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona), patch(
            f"{_ACTION_MODULE}.AgentInteractAction._phase_execute_skill_loop",
            new_callable=AsyncMock,
        ) as mock_skill:
            await action.execute(visitor)

        mock_skill.assert_called_once_with(visitor, result)

    @pytest.mark.asyncio
    async def test_null_routing_result_defaults_to_conv(self):
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        object.__setattr__(action, "response_mode", "publish")
        visitor = _make_visitor()

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, None),
        ), _patch_require_persona(persona):
            await action.execute(visitor)

        persona.respond_slim.assert_called_once()

    @pytest.mark.asyncio
    async def test_conversational_respond_mode_calls_respond_not_slim_lm(self):
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        object.__setattr__(action, "response_mode", "respond")
        visitor = _make_visitor()

        result = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
        )

        persona = _make_persona_mock()

        with patch(
            f"{_ROUTER_MODULE}.AgentInteractRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, result),
        ), _patch_require_persona(persona), patch(
            "jvagent.action.interact.base.InteractAction.respond",
            new_callable=AsyncMock,
        ) as mock_respond:
            await action.execute(visitor)

        visitor.add_directive.assert_called_once()
        mock_respond.assert_called_once()
        persona.respond_slim.assert_not_called()

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
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        descriptors = {
            "outlook_mail": {"description": "Send/list emails"},
            "google_drive": {"description": "Upload/list files"},
        }
        result = AgentInteractRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "google_drive"], descriptors
        )
        assert result == ["outlook_mail", "google_drive"]

    def test_drops_invalid_skill_names(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        descriptors = {"outlook_mail": {"description": "Send emails"}}
        result = AgentInteractRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "made_up_skill", "gibberish"], descriptors
        )
        assert result == ["outlook_mail"]

    def test_deduplication(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        result = AgentInteractRouter._resolve_skill_names_to_keys(
            ["outlook_mail", "outlook_mail"], descriptors
        )
        assert result == ["outlook_mail"]

    def test_empty_input(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        assert AgentInteractRouter._resolve_skill_names_to_keys([], descriptors) == []

    def test_empty_strings_skipped(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        descriptors = {"outlook_mail": {"description": "..."}}
        result = AgentInteractRouter._resolve_skill_names_to_keys(
            ["", "outlook_mail", " "], descriptors
        )
        assert result == ["outlook_mail"]


class TestMergeRouteTargets:
    def test_merges_skills_and_interact_actions(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        action = AgentInteractAction()
        router = AgentInteractRouter(action)
        skill_desc = {"s1": {"description": "skill one"}}
        ia_desc = {
            "HandoffInteractAction": {"kind": "interact_action", "description": ""}
        }
        out = router._merge_and_validate_routes(
            ["s1", "unknown_skill"],
            ["HandoffInteractAction", "UnknownIA"],
            skill_desc,
            ia_desc,
        )
        assert out == ["s1", "HandoffInteractAction"]

    def test_recovers_interact_action_listed_under_skills(self):
        from jvagent.action.agent_interact.router.service import AgentInteractRouter

        action = AgentInteractAction()
        router = AgentInteractRouter(action)
        skill_desc = {"s1": {"description": "x"}}
        ia_desc = {
            "PageIndexInteractAction": {"kind": "interact_action", "description": ""}
        }
        out = router._merge_and_validate_routes(
            ["PageIndexInteractAction"],
            [],
            skill_desc,
            ia_desc,
        )
        assert out == ["PageIndexInteractAction"]


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

        assert action._language_model_action_type_for_purpose("skill") == (
            "OllamaLanguageModelAction"
        )
        assert action._language_model_action_type_for_purpose("router") == (
            "OpenAILanguageModelAction"
        )

    def test_router_falls_back_to_skill_type(self):
        action = AgentInteractAction()
        object.__setattr__(action, "model_action_type", "AnthropicLanguageModelAction")
        assert action._language_model_action_type_for_purpose("router") == (
            "AnthropicLanguageModelAction"
        )


# ---------------------------------------------------------------------------
# Persona directive formatting
# ---------------------------------------------------------------------------


class TestPersonaDirective:
    def test_format_persona_directive_is_tell_the_user(self):
        d = AgentInteractAction._format_persona_directive("A, B, C")
        assert d == "Tell the user: A, B, C"

    def test_format_persona_directive_strips_whitespace(self):
        d = AgentInteractAction._format_persona_directive("  Result  ")
        assert d == "Tell the user: Result"

    def test_conversational_directive_is_instructions_only(self):
        d = format_conversational_directive_for_persona("Be brief.")
        assert d == "Be brief."
        assert "CONVERSATIONAL_TURN" not in d
        assert "User said" not in d

    def test_conversational_directive_fallback_when_empty_instructions(self):
        d = format_conversational_directive_for_persona("")
        assert "brief" in d.lower()


class TestSkillLoopDeliverability:
    def test_deliverable_false_for_blank(self):
        assert _skill_loop_output_is_deliverable("") is False
        assert _skill_loop_output_is_deliverable("   ") is False

    def test_deliverable_false_for_empty_tool_lines_only(self):
        msg = "Tool `foo` returned empty output."
        assert _skill_loop_output_is_deliverable(msg) is False

    def test_deliverable_true_for_real_content(self):
        assert _skill_loop_output_is_deliverable("Here is the answer.") is True

    def test_deliverable_true_when_empty_tool_line_plus_substance(self):
        msg = "Tool `foo` returned empty output.\n\nHours are 9–5."
        assert _skill_loop_output_is_deliverable(msg) is True

    def test_skill_slim_publish_prompt_contains_draft(self):
        p = AgentInteractAction._format_skill_slim_publish_prompt("Q?", "Draft answer")
        assert "Q?" in p
        assert "Draft answer" in p

    def test_slim_prompt_forbids_inventing_details(self):
        p = AgentInteractAction._format_skill_slim_publish_prompt("Q?", "Draft answer")
        assert "MUST NOT add it" in p
        assert "MUST NOT add it" in p
        assert "deliberately generic" in p.lower() or "keep it generic" in p.lower()
        assert "Do not invent examples" in p or "invent examples" in p.lower()


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
