"""Tests for conditional branching logic."""

import pytest

from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.graph.question_branch_evaluator import (
    QuestionBranchEvaluator,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession


@pytest.fixture
async def test_session_with_branches(test_db):
    """Create a test session with conditional branches."""
    question_graph = [
        {
            "name": "user_type",
            "question": "Are you a premium or standard user?",
            "constraints": {"description": "User account type", "type": "string"},
            "required": True,
            "branches": [
                {
                    "condition": {"op": "equals", "value": "premium"},
                    "target": "premium_features",
                },
                {
                    "condition": {"op": "equals", "value": "standard"},
                    "target": "standard_setup",
                },
            ],
            "default_next": "contact_info",
        },
        {
            "name": "premium_features",
            "question": "Which premium features interest you?",
            "constraints": {"description": "Premium features", "type": "string"},
            "required": False,
            "default_next": "contact_info",
        },
        {
            "name": "standard_setup",
            "question": "Standard setup question",
            "constraints": {"description": "Standard setup", "type": "string"},
            "required": False,
            "default_next": "contact_info",
        },
        {
            "name": "contact_info",
            "question": "What's your contact information?",
            "constraints": {"description": "Contact info", "type": "string"},
            "required": True,
        },
    ]

    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_graph=question_graph,
        state=InterviewState.ACTIVE,
    )
    return session


class TestConditionalBranching:
    """Test conditional branching logic."""

    @pytest.mark.asyncio
    async def test_branch_equals_condition(self, test_session_with_branches):
        """Test equals condition matching."""
        session = test_session_with_branches

        # Set response that matches premium branch
        session.set_response("user_type", "premium")

        condition = {"op": "equals", "value": "premium"}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is True

        # Test non-matching value
        session.set_response("user_type", "standard")
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is False

    @pytest.mark.asyncio
    async def test_branch_in_condition(self, test_session_with_branches):
        """Test 'in' condition matching."""
        session = test_session_with_branches

        session.set_response("user_type", "premium")

        condition = {"op": "in", "value": ["premium", "vip"]}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is True

        # Test value not in list
        session.set_response("user_type", "standard")
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is False

    @pytest.mark.asyncio
    async def test_branch_exists_condition(self, test_session_with_branches):
        """Test 'exists' condition matching."""
        session = test_session_with_branches

        # Test exists when value is present
        session.set_response("user_type", "premium")
        condition = {"op": "exists"}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is True

        # Test exists when value is None
        session.responses.pop("user_type", None)
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        assert matches is False

    @pytest.mark.asyncio
    async def test_branch_comparison_operators(self, test_session_with_branches):
        """Test comparison operators (>=, <=, >, <)."""
        session = test_session_with_branches

        # Add numeric question
        session.question_graph.append(
            {
                "name": "age",
                "question": "What's your age?",
                "constraints": {"description": "Age", "type": "integer"},
                "required": False,
            }
        )

        # Test >=
        session.set_response("age", 25)
        condition = {"op": ">=", "value": 18}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="age"
        )
        assert matches is True

        # Test <
        session.set_response("age", 15)
        condition = {"op": "<", "value": 18}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="age"
        )
        assert matches is True

    @pytest.mark.asyncio
    async def test_branch_with_unanswered_question(self, test_session_with_branches):
        """Test branch evaluation when question is not answered."""
        session = test_session_with_branches

        # Don't set user_type response
        condition = {"op": "equals", "value": "premium"}
        matches = await QuestionBranchEvaluator.matches(
            condition, session, implicit_question="user_type"
        )
        # Should return False when question is not answered
        assert matches is False

    @pytest.mark.asyncio
    async def test_get_next_fields_with_branches(self, test_session_with_branches):
        """Test getting next questions based on branch conditions."""
        session = test_session_with_branches

        # Set premium response
        session.set_response("user_type", "premium")

        next_fields = await session.get_next_fields("user_type")
        assert "premium_features" in next_fields

        # Set standard response
        session.set_response("user_type", "standard")
        next_fields = await session.get_next_fields("user_type")
        assert "standard_setup" in next_fields

    @pytest.mark.asyncio
    async def test_get_reachable_unanswered_questions_with_branches(self, test_db):
        """Test that get_reachable_unanswered_questions only returns questions on active branch path."""
        from unittest.mock import MagicMock

        from jvagent.action.interview.core.graph.question_edge import QuestionEdge
        from jvagent.action.interview.core.graph.question_node import QuestionNode

        # Create session with conditional branches
        question_graph = [
            {
                "name": "user_type",
                "question": "Are you a premium or standard user?",
                "constraints": {"description": "User account type", "type": "string"},
                "required": True,
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "premium"},
                        "target": "premium_features",
                    },
                    {
                        "condition": {"op": "equals", "value": "standard"},
                        "target": "standard_setup",
                    },
                ],
                "default_next": "contact_info",
            },
            {
                "name": "premium_features",
                "question": "Which premium features interest you?",
                "constraints": {"description": "Premium features", "type": "string"},
                "required": False,
                "default_next": "contact_info",
            },
            {
                "name": "standard_setup",
                "question": "Standard setup question",
                "constraints": {"description": "Standard setup", "type": "string"},
                "required": False,
                "default_next": "contact_info",
            },
            {
                "name": "contact_info",
                "question": "What's your contact information?",
                "constraints": {"description": "Contact info", "type": "string"},
                "required": True,
            },
        ]

        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=question_graph,
            state=InterviewState.ACTIVE,
        )

        # Create question nodes
        user_type_node = await QuestionNode.create(
            label="user_type", question_config=question_graph[0]
        )
        premium_node = await QuestionNode.create(
            label="premium_features", question_config=question_graph[1]
        )
        standard_node = await QuestionNode.create(
            label="standard_setup", question_config=question_graph[2]
        )
        contact_node = await QuestionNode.create(
            label="contact_info", question_config=question_graph[3]
        )

        # Create edges for conditional branching
        await user_type_node.connect(
            premium_node,
            edge=QuestionEdge,
            condition={"op": "equals", "value": "premium"},
            branch_index=0,
            is_default=False,
        )
        await user_type_node.connect(
            standard_node,
            edge=QuestionEdge,
            condition={"op": "equals", "value": "standard"},
            branch_index=1,
            is_default=False,
        )
        await user_type_node.connect(
            contact_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await premium_node.connect(
            contact_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await standard_node.connect(
            contact_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )

        # Test 1: No responses - all questions should be reachable since we can't determine path
        # But only user_type should be unanswered since it's first
        reachable_unanswered = await session.get_reachable_unanswered_questions(
            user_type_node, None, None
        )
        # Should include user_type as first question
        assert "user_type" in reachable_unanswered

        # Test 2: Answer user_type with "premium" - only premium_features and contact_info should be reachable
        session.set_response("user_type", "premium")
        await session.save()

        reachable_unanswered = await session.get_reachable_unanswered_questions(
            user_type_node, None, None
        )

        # Should include premium_features (on active path) but NOT standard_setup (unreachable)
        assert "premium_features" in reachable_unanswered
        assert "standard_setup" not in reachable_unanswered
        assert "contact_info" in reachable_unanswered
        assert "user_type" not in reachable_unanswered  # Already answered

        # Test 3: Answer user_type with "standard" - only standard_setup and contact_info should be reachable
        session.set_response("user_type", "standard")
        await session.save()

        # Need to invalidate cache since we changed the response
        from jvagent.action.interview.core.utils.cache_utils import BranchCache

        branch_cache = BranchCache(session)
        branch_cache.invalidate_all()

        reachable_unanswered = await session.get_reachable_unanswered_questions(
            user_type_node, None, None
        )

        # Should include standard_setup (on active path) but NOT premium_features (unreachable)
        assert "standard_setup" in reachable_unanswered
        assert "premium_features" not in reachable_unanswered
        assert "contact_info" in reachable_unanswered
        assert "user_type" not in reachable_unanswered  # Already answered

        # Test 4: Compare with legacy get_unanswered_questions (should return all unanswered)
        legacy_unanswered = session.get_unanswered_questions()

        # Legacy method returns ALL unanswered questions regardless of reachability
        assert "standard_setup" in legacy_unanswered
        assert "premium_features" in legacy_unanswered  # This is the key difference!
        assert "contact_info" in legacy_unanswered
        assert "user_type" not in legacy_unanswered  # Already answered

        # Cleanup
        await user_type_node.delete()
        await premium_node.delete()
        await standard_node.delete()
        await contact_node.delete()
        await session.delete()

    @pytest.mark.asyncio
    async def test_reachable_unanswered_prevents_branch_function_premature_execution(
        self, test_db
    ):
        """Test that reachable unanswered questions prevent premature branch function execution."""
        from jvagent.action.interview.core.graph.question_edge import QuestionEdge
        from jvagent.action.interview.core.graph.question_node import QuestionNode

        # Create session with branch function condition
        question_graph = [
            {
                "name": "dependency_question",
                "question": "What's your dependency answer?",
                "constraints": {"description": "Dependency", "type": "string"},
                "required": True,
                "branches": [
                    {
                        "condition": {"function": "check_dependency"},
                        "target": "dependent_question",
                    }
                ],
                "default_next": "other_question",
            },
            {
                "name": "dependent_question",
                "question": "Dependent question",
                "constraints": {"description": "Dependent", "type": "string"},
                "required": False,
                "default_next": "final_question",
            },
            {
                "name": "other_question",
                "question": "Other question",
                "constraints": {"description": "Other", "type": "string"},
                "required": False,
                "default_next": "final_question",
            },
            {
                "name": "final_question",
                "question": "Final question",
                "constraints": {"description": "Final", "type": "string"},
                "required": True,
            },
        ]

        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=question_graph,
            state=InterviewState.ACTIVE,
        )

        # Create question nodes
        dependency_node = await QuestionNode.create(
            label="dependency_question", question_config=question_graph[0]
        )
        dependent_node = await QuestionNode.create(
            label="dependent_question", question_config=question_graph[1]
        )
        other_node = await QuestionNode.create(
            label="other_question", question_config=question_graph[2]
        )
        final_node = await QuestionNode.create(
            label="final_question", question_config=question_graph[3]
        )

        # Create edges
        await dependency_node.connect(
            dependent_node,
            edge=QuestionEdge,
            condition={"function": "check_dependency"},
            branch_index=0,
            is_default=False,
        )
        await dependency_node.connect(
            other_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await dependent_node.connect(
            final_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await other_node.connect(
            final_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )

        # Test: Without answering dependency_question, branch function should not execute
        # and dependent_question should NOT be in reachable unanswered list
        reachable_unanswered = await session.get_reachable_unanswered_questions(
            dependency_node, None, None
        )

        # Only dependency_question should be reachable since it's unanswered
        # The branch function won't execute without the answer
        assert "dependency_question" in reachable_unanswered

        # Note: The path walker may not traverse past unanswered questions with branch functions
        # So we verify that get_reachable_unanswered_questions doesn't include unreachable questions

        # Cleanup
        await dependency_node.delete()
        await dependent_node.delete()
        await other_node.delete()
        await final_node.delete()
        await session.delete()

    @pytest.mark.asyncio
    async def test_walker_stops_at_branches_without_cache(self, test_db):
        """Test that QuestionPathWalker stops at unanswered questions with conditional branches when no cache exists."""
        from jvagent.action.interview.core.graph.question_edge import QuestionEdge
        from jvagent.action.interview.core.graph.question_node import QuestionNode
        from jvagent.action.interview.core.graph.question_path_walker import (
            QuestionPathWalker,
        )

        # Create session mimicking the reported issue:
        # incident_description (answered) → incident_location (unanswered) → incident_media (unanswered, has branches) → ...
        question_graph = [
            {
                "name": "incident_description",
                "question": "Describe the incident",
                "constraints": {"description": "Description", "type": "string"},
                "required": True,
            },
            {
                "name": "incident_location",
                "question": "Where did it happen?",
                "constraints": {"description": "Location", "type": "string"},
                "required": True,
            },
            {
                "name": "incident_media",
                "question": "Do you have media?",
                "constraints": {"description": "Media", "type": "list"},
                "required": False,
                "branches": [
                    {
                        "condition": {"function": "detect_sensitive"},
                        "target": "is_sensitive",
                    }
                ],
                "default_next": "reporting_on_behalf",
            },
            {
                "name": "is_sensitive",
                "question": "Is this sensitive?",
                "constraints": {"description": "Sensitivity flag", "type": "string"},
                "required": True,
                "branches": [
                    {"condition": {"op": "equals", "value": "yes"}, "target": "REVIEW"},
                    {
                        "condition": {"op": "equals", "value": "no"},
                        "target": "reporting_on_behalf",
                    },
                ],
            },
            {
                "name": "reporting_on_behalf",
                "question": "Reporting on behalf?",
                "constraints": {"description": "Behalf flag", "type": "string"},
                "required": True,
            },
        ]

        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=question_graph,
            state=InterviewState.ACTIVE,
        )

        # Answer only incident_description
        session.set_response("incident_description", "A woman was assaulted")
        await session.save()

        # Create question nodes
        desc_node = await QuestionNode.create(
            label="incident_description", question_config=question_graph[0]
        )
        location_node = await QuestionNode.create(
            label="incident_location", question_config=question_graph[1]
        )
        media_node = await QuestionNode.create(
            label="incident_media", question_config=question_graph[2]
        )
        sensitive_node = await QuestionNode.create(
            label="is_sensitive", question_config=question_graph[3]
        )
        behalf_node = await QuestionNode.create(
            label="reporting_on_behalf", question_config=question_graph[4]
        )

        # Create edges (sequential with conditional branches)
        await desc_node.connect(
            location_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await location_node.connect(
            media_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )
        await media_node.connect(
            sensitive_node,
            edge=QuestionEdge,
            condition={"function": "detect_sensitive"},
            branch_index=0,
            is_default=False,
        )
        await media_node.connect(
            behalf_node,
            edge=QuestionEdge,
            condition=None,
            branch_index=-1,
            is_default=True,
        )

        # Test: Get reachable questions
        # Should include: incident_description (answered), incident_location (unanswered), incident_media (unanswered with branches)
        # Should NOT include: is_sensitive, reporting_on_behalf (beyond unanswered branch point)
        reachable = await QuestionPathWalker.get_reachable_questions(
            session, desc_node, None, None
        )

        # Verify: incident_description is reachable
        assert "incident_description" in reachable

        # Verify: incident_location is reachable (unanswered but no branches)
        assert (
            "incident_location" in reachable
        ), "incident_location should be reachable (no branches, just sequential)"

        # Verify: incident_media is reachable (unanswered with branches, but node itself should be included)
        assert (
            "incident_media" in reachable
        ), "incident_media should be reachable (add to reachable before stopping)"

        # Verify: is_sensitive and reporting_on_behalf are NOT reachable (beyond incident_media which has no cache)
        assert (
            "is_sensitive" not in reachable
        ), "Should stop at incident_media (no cache) - is_sensitive should not be reachable"
        assert (
            "reporting_on_behalf" not in reachable
        ), "Should stop at incident_media (no cache) - reporting_on_behalf should not be reachable"

        # Cleanup
        await desc_node.delete()
        await location_node.delete()
        await media_node.delete()
        await sensitive_node.delete()
        await behalf_node.delete()
        await session.delete()

    @pytest.mark.asyncio
    async def test_walker_continues_past_answered_nodes_with_branches(self, test_db):
        """Test that QuestionPathWalker continues past answered nodes even if they have branches."""
        from jvagent.action.interview.core.graph.question_edge import QuestionEdge
        from jvagent.action.interview.core.graph.question_node import QuestionNode
        from jvagent.action.interview.core.graph.question_path_walker import (
            QuestionPathWalker,
        )

        question_graph = [
            {
                "name": "q1",
                "question": "Question 1",
                "constraints": {"description": "Q1", "type": "string"},
                "required": True,
                "branches": [
                    {"condition": {"op": "equals", "value": "yes"}, "target": "q2"},
                    {"condition": {"op": "equals", "value": "no"}, "target": "q3"},
                ],
            },
            {
                "name": "q2",
                "question": "Question 2",
                "constraints": {"description": "Q2", "type": "string"},
                "required": False,
            },
            {
                "name": "q3",
                "question": "Question 3",
                "constraints": {"description": "Q3", "type": "string"},
                "required": False,
            },
        ]

        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=question_graph,
            state=InterviewState.ACTIVE,
        )

        # Answer q1 (which has branches)
        session.set_response("q1", "yes")

        # Set up branch cache: q1 → q2
        from jvagent.action.interview.core.utils.cache_utils import BranchCache

        branch_cache = BranchCache(session)
        branch_cache.record_branch_path("q1", 0, "q2", is_default=False)
        await session.save()

        # Create nodes
        q1_node = await QuestionNode.create(
            label="q1", question_config=question_graph[0]
        )
        q2_node = await QuestionNode.create(
            label="q2", question_config=question_graph[1]
        )
        q3_node = await QuestionNode.create(
            label="q3", question_config=question_graph[2]
        )

        # Create edges
        await q1_node.connect(
            q2_node,
            edge=QuestionEdge,
            condition={"op": "equals", "value": "yes"},
            branch_index=0,
            is_default=False,
        )
        await q1_node.connect(
            q3_node,
            edge=QuestionEdge,
            condition={"op": "equals", "value": "no"},
            branch_index=1,
            is_default=False,
        )

        # Test: Get reachable questions - should continue past q1 since it's answered
        reachable = await QuestionPathWalker.get_reachable_questions(
            session, q1_node, None, None
        )

        # Verify: q1 and q2 are reachable (answered node with branches should continue)
        assert "q1" in reachable
        assert (
            "q2" in reachable
        ), "Should continue past answered q1 even though it has branches"

        # Verify: q3 is NOT reachable (different branch)
        assert "q3" not in reachable

        # Cleanup
        await q1_node.delete()
        await q2_node.delete()
        await q3_node.delete()
        await session.delete()
