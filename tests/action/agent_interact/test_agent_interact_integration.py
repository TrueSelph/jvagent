"""Integration tests for agent_interact_action: E2E flows and migration compat."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction
from jvagent.action.router.routing_result import (
    POSTURE_RESPOND,
    RoutingResult,
    parse_routing_response,
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

    actions_mgr = MagicMock()
    actions_mgr.get_actions = AsyncMock(return_value=[])

    visitor.data = {}
    visitor.unrecord_action_execution = AsyncMock()
    visitor._agent = MagicMock()
    visitor._agent.namespace = "test"
    visitor._agent.name = "test_agent"
    visitor._agent.get_actions_manager = AsyncMock(return_value=actions_mgr)
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
# Full flow: Conversational
# ---------------------------------------------------------------------------


class TestFullFlowConversational:
    @pytest.mark.asyncio
    async def test_hello_triggers_converse_path_and_publishes(self):
        """E2E: "Hello" → route (CONVERSATIONAL) → converse path → publish."""
        action = AgentInteractAction()
        object.__setattr__(action, "converse_enabled", True)
        visitor = _make_visitor()
        visitor.interaction.utterance = "Hello"

        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="CONVERSATIONAL",
            actions=[],
            confidence=1.0,
            canned_response="",
        )

        conv_model = MagicMock()
        conv_model.generate = AsyncMock(return_value="Hi there! How can I help?")
        conv_model.provider = "openai"
        conv_model.model = "gpt-4o-mini"

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=conv_model,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await action.execute(visitor)

        conv_model.generate.assert_called_once()
        mock_publish.assert_called_once_with(
            visitor,
            content="Hi there! How can I help?",
            streaming_complete=True,
        )


# ---------------------------------------------------------------------------
# Full flow: Skill-based
# ---------------------------------------------------------------------------


class TestFullFlowSkillBased:
    @pytest.mark.asyncio
    async def test_skill_intent_triggers_skill_loop_and_publishes(self):
        """E2E: "Send email" → route → skill loop → publish result."""
        from jvagent.action.skill.skill_action_contracts import (
            SkillRunResult,
            TerminationReason,
        )

        action = AgentInteractAction()
        visitor = _make_visitor()
        visitor.interaction.utterance = "Send email to Bob"

        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="DIRECTIVE",
            actions=["outlook_mail"],
            confidence=0.95,
        )

        skill_result = SkillRunResult(
            final_response="Email sent to Bob.",
            termination_reason=TerminationReason.COMPLETED,
            stuck_corrections=0,
            result_attributions=[],
            iterations=3,
            duration_seconds=1.5,
            task_id="task_1",
            activated_skills=["outlook_mail"],
        )

        model = AsyncMock()
        model.generate = AsyncMock()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
            new_callable=AsyncMock,
            return_value=skill_result,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await action.execute(visitor)

        # result.final_response should be published
        mock_publish.assert_called_once_with(
            visitor,
            content="Email sent to Bob.",
            streaming_complete=True,
        )

    @pytest.mark.asyncio
    async def test_skill_loop_passes_preloaded_skills_to_context(self):
        """Verify router-selected skills are passed as preloaded_skills."""
        from jvagent.action.skill.skill_action_contracts import (
            SkillRunResult,
            TerminationReason,
        )

        action = AgentInteractAction()
        visitor = _make_visitor()
        visitor.interaction.utterance = "Check my calendar"

        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="DIRECTIVE",
            actions=["outlook_calendar", "answer"],
            confidence=0.9,
        )

        skill_result = SkillRunResult(
            final_response="Your calendar is clear.",
            termination_reason=TerminationReason.COMPLETED,
            stuck_corrections=0,
            result_attributions=[],
            iterations=2,
            duration_seconds=0.8,
            task_id="task_2",
            activated_skills=["outlook_calendar"],
        )

        captured_ctx = []

        async def capture_ctx(ctx):
            captured_ctx.append(ctx)
            return skill_result

        model = AsyncMock()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
            side_effect=capture_ctx,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ), patch(
            "jvagent.action.agent_interact.agent_interact_action.AgentInteractAction._get_always_active_skills",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await action.execute(visitor)

        assert len(captured_ctx) == 1
        ctx = captured_ctx[0]
        assert "outlook_calendar" in ctx.preloaded_skills
        assert "answer" in ctx.preloaded_skills


# ---------------------------------------------------------------------------
# Regression: SkillRouter LLM output uses `skills`, not `actions`
# ---------------------------------------------------------------------------


class TestSkillRouterSkillsFieldRegression:
    """End-to-end regression for the bug where an LLM response with the
    ``skills`` field (per ``SKILL_ROUTING_PROMPT_TEMPLATE``) was silently
    parsed as ``actions=[]`` and routed to the converse fast path
    instead of dispatching the agentic skill loop."""

    @pytest.mark.asyncio
    async def test_router_skills_field_dispatches_to_skill_loop(self):
        """LLM output `{"skills": ["web_search"]}` must reach the skill loop."""
        from jvagent.action.skill.skill_action_contracts import (
            SkillRunResult,
            TerminationReason,
        )

        # The exact router LLM output shape from the bug report.
        router_llm_output = json.dumps(
            {
                "posture": "RESPOND",
                "interpretation": "User is requesting information about Eldon Marks.",
                "intent_type": "INFORMATIONAL",
                "skills": ["web_search"],
                "confidence": 0.9,
                "canned_response": "Looking into that now",
            }
        )
        routing = parse_routing_response(router_llm_output)

        # Sanity: parser maps skills -> actions
        assert routing.actions == ["web_search"]
        assert routing.intent_type == "INFORMATIONAL"

        action = AgentInteractAction()
        visitor = _make_visitor()
        visitor.interaction.utterance = "Tell me about Eldon Marks"

        skill_result = SkillRunResult(
            final_response="Eldon Marks is ...",
            termination_reason=TerminationReason.COMPLETED,
            stuck_corrections=0,
            result_attributions=[],
            iterations=2,
            duration_seconds=0.7,
            task_id="task_regression",
            activated_skills=["web_search"],
        )

        captured_ctx = []

        async def capture_ctx(ctx):
            captured_ctx.append(ctx)
            return skill_result

        model = AsyncMock()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
            side_effect=capture_ctx,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ) as mock_publish, patch(
            f"{_ACTION_MODULE}.AgentInteractAction._get_always_active_skills",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await action.execute(visitor)

        # Skill loop ran (proves we didn't fall through to converse path) and
        # received the router-selected skill as a preload.
        assert len(captured_ctx) == 1, (
            "skill loop was not invoked; router likely fell through to "
            "converse fast path"
        )
        assert "web_search" in captured_ctx[0].preloaded_skills
        mock_publish.assert_called_once_with(
            visitor,
            content="Eldon Marks is ...",
            streaming_complete=True,
        )


# ---------------------------------------------------------------------------
# Migration compatibility
# ---------------------------------------------------------------------------


class TestMigrationCompatibility:
    def test_legacy_pair_produces_warning_when_validated(self):
        """Legacy interact_router + skill_interact_action config emits deprecation warning."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/interact_router", "context": {}},
            {"action": "jvagent/skill_interact_action", "context": {}},
        ]

        with patch.object(
            logging.getLogger("jvagent.core.agent_loader"), "warning"
        ) as mock_warn:
            _validate_interact_routing_config(actions)

        mock_warn.assert_called_once()
        assert "deprecated" in mock_warn.call_args[0][0].lower()

    def test_new_action_without_legacy_passes(self):
        """agent_interact_action alone passes validation silently."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/agent_interact_action", "context": {}},
        ]

        with patch.object(
            logging.getLogger("jvagent.core.agent_loader"), "warning"
        ) as mock_warn:
            _validate_interact_routing_config(actions)

        mock_warn.assert_not_called()

    def test_only_router_without_skill_passes(self):
        """interact_router alone is valid (no required pairing with skill_interact_action)."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/interact_router", "context": {}},
        ]

        with patch.object(
            logging.getLogger("jvagent.core.agent_loader"), "warning"
        ) as mock_warn:
            _validate_interact_routing_config(actions)

        mock_warn.assert_not_called()

    def test_only_skill_without_router_passes(self):
        """skill_interact_action alone is valid (no required pairing with interact_router)."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/skill_interact_action", "context": {}},
        ]

        with patch.object(
            logging.getLogger("jvagent.core.agent_loader"), "warning"
        ) as mock_warn:
            _validate_interact_routing_config(actions)

        mock_warn.assert_not_called()

    def test_no_interact_actions_passes_silently(self):
        """Agent with no interact-related actions passes validation."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/persona", "context": {}},
            {"action": "custom/some_utility_action", "context": {}},
        ]

        _validate_interact_routing_config(actions)  # should not raise

    def test_new_action_with_legacy_warns(self):
        """agent_interact_action + legacy actions warn about redundancy."""
        from jvagent.core.agent_loader import _validate_interact_routing_config

        actions = [
            {"action": "jvagent/agent_interact_action", "context": {}},
            {"action": "jvagent/interact_router", "context": {}},
        ]

        with patch.object(
            logging.getLogger("jvagent.core.agent_loader"), "warning"
        ) as mock_warn:
            _validate_interact_routing_config(actions)

        mock_warn.assert_called_once()


# ---------------------------------------------------------------------------
# Always-active skills are passed into the SkillRunContext
# ---------------------------------------------------------------------------


class TestAlwaysActiveIntegration:
    @pytest.mark.asyncio
    async def test_always_active_skills_merged_into_preloaded(self):
        from jvagent.action.skill.skill_action_contracts import (
            SkillRunResult,
            TerminationReason,
        )

        action = AgentInteractAction()
        visitor = _make_visitor()
        visitor.interaction.utterance = "Do research"

        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="INFORMATIONAL",
            actions=["research"],
            confidence=0.85,
        )

        skill_result = SkillRunResult(
            final_response="Research complete.",
            termination_reason=TerminationReason.COMPLETED,
            stuck_corrections=0,
            result_attributions=[],
            iterations=3,
            duration_seconds=1.0,
            task_id="task_3",
            activated_skills=["research", "triage"],
        )

        captured_ctx = []

        async def capture_ctx(ctx):
            captured_ctx.append(ctx)
            return skill_result

        model = AsyncMock()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
            side_effect=capture_ctx,
        ), patch(
            "jvagent.action.interact.base.InteractAction.publish",
            new_callable=AsyncMock,
        ), patch(
            "jvagent.action.agent_interact.agent_interact_action.AgentInteractAction._get_always_active_skills",
            new_callable=AsyncMock,
            return_value=["triage"],
        ):
            await action.execute(visitor)

        assert len(captured_ctx) == 1
        ctx = captured_ctx[0]
        assert "research" in ctx.preloaded_skills
        assert "triage" in ctx.preloaded_skills


# ---------------------------------------------------------------------------
# Persona-based response delivery (response_mode="respond")
# ---------------------------------------------------------------------------


class TestPersonaResponseDelivery:
    @pytest.mark.asyncio
    async def test_respond_mode_injects_persona_directive(self):
        from jvagent.action.skill.skill_action_contracts import (
            SkillRunResult,
            TerminationReason,
        )

        action = AgentInteractAction()
        object.__setattr__(action, "response_mode", "respond")
        visitor = _make_visitor()
        visitor.interaction.utterance = "What are jvagent's skills?"

        routing = RoutingResult(
            posture=POSTURE_RESPOND,
            intent_type="INFORMATIONAL",
            actions=["answer"],
            confidence=0.9,
        )

        skill_result = SkillRunResult(
            final_response="jvagent has many skills including outlook_mail and google_drive.",
            termination_reason=TerminationReason.COMPLETED,
            stuck_corrections=0,
            result_attributions=[],
            iterations=2,
            duration_seconds=1.0,
            task_id="task_4",
            activated_skills=["answer"],
        )

        model = AsyncMock()

        with patch(
            f"{_ROUTER_MODULE}.SkillRouter.route",
            new_callable=AsyncMock,
            return_value=(POSTURE_RESPOND, routing),
        ), patch(
            f"{_ACTION_MODULE}.AgentInteractAction.get_model_action",
            new_callable=AsyncMock,
            return_value=model,
        ), patch(
            "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
            new_callable=AsyncMock,
            return_value=skill_result,
        ), patch(
            "jvagent.action.interact.base.InteractAction.respond",
            new_callable=AsyncMock,
        ) as mock_respond:
            await action.execute(visitor)

        # Should have called add_directive FIRST then respond
        visitor.add_directive.assert_called_once()
        mock_respond.assert_called_once_with(visitor)
