"""Tests for InterviewWalker traversal logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.foundation.enums import Intent, InterviewState
from jvagent.action.interview.core.graph.interview_walker import InterviewWalker
from jvagent.action.interview.core.graph.question_node import QuestionNode
from jvagent.action.interview.core.processing.target_resolver import TargetResolver
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.interview_interact_action import InterviewInteractAction


@pytest.fixture
async def test_session(test_db):
    """Create a test interview session."""
    question_graph = [
        {
            "name": "q1",
            "question": "Question 1?",
            "constraints": {"description": "First question", "type": "string"},
            "required": True,
        },
        {
            "name": "q2",
            "question": "Question 2?",
            "constraints": {"description": "Second question", "type": "string"},
            "required": True,
        },
        {
            "name": "q3",
            "question": "Question 3?",
            "constraints": {"description": "Third question", "type": "string"},
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
    return session


@pytest.fixture
def mock_interview_action():
    """Create a mock interview action."""
    action = MagicMock()
    action.get_class_name = MagicMock(return_value="TestInterviewAction")
    return action


class TestInterviewWalker:
    """Test InterviewWalker functionality."""

    @pytest.mark.asyncio
    async def test_find_next_unanswered_question(
        self, test_session, mock_interview_action
    ):
        """Test finding next unanswered question."""
        walker = InterviewWalker()
        walker.interview_session = test_session

        # No questions answered yet
        unanswered = test_session.get_unanswered_questions()
        assert "q1" in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered

        # Answer first question
        test_session.set_response("q1", "answer1")

        unanswered = test_session.get_unanswered_questions()
        assert "q1" not in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered

    @pytest.mark.asyncio
    async def test_get_answered_questions(self, test_session):
        """Test getting answered questions."""
        assert len(test_session.get_answered_questions()) == 0

        test_session.set_response("q1", "answer1")
        test_session.set_response("q2", "answer2")

        answered = test_session.get_answered_questions()
        assert "q1" in answered
        assert "q2" in answered
        assert len(answered) == 2

    @pytest.mark.asyncio
    async def test_get_unanswered_questions(self, test_session):
        """Test getting unanswered questions."""
        unanswered = test_session.get_unanswered_questions()
        assert len(unanswered) == 3
        assert "q1" in unanswered
        assert "q2" in unanswered
        assert "q3" in unanswered

        test_session.set_response("q1", "answer1")
        unanswered = test_session.get_unanswered_questions()
        assert "q1" not in unanswered
        assert len(unanswered) == 2

    @pytest.mark.asyncio
    async def test_get_required_questions(self, test_session):
        """Test getting required questions."""
        required = test_session.get_required_questions()
        assert "q1" in required
        assert "q2" in required
        assert "q3" not in required  # Optional
        assert len(required) == 2

    @pytest.mark.asyncio
    async def test_state_target_detection(self):
        """Test state target detection."""
        walker = InterviewWalker()

        assert walker._is_state_target("REVIEW") is True
        assert walker._is_state_target("COMPLETED") is True
        assert walker._is_state_target("CANCELLED") is True
        assert walker._is_state_target("ACTIVE") is False  # Not a state target
        assert walker._is_state_target("question_name") is False

    @pytest.mark.asyncio
    async def test_get_state_from_target(self):
        """Test getting InterviewState from target string."""
        walker = InterviewWalker()

        from jvagent.action.interview.core.foundation.enums import InterviewState

        assert walker._get_state_from_target("REVIEW") == InterviewState.REVIEW
        assert walker._get_state_from_target("COMPLETED") == InterviewState.COMPLETED
        assert walker._get_state_from_target("CANCELLED") == InterviewState.CANCELLED
        assert walker._get_state_from_target("invalid") is None


class TestResolveTargetNodeOutOfOrder:
    """Test that answering a non-current question does not skip preceding questions."""

    @pytest.mark.asyncio
    async def test_submission_resolves_to_first_question_not_last_answered(
        self, test_db
    ):
        """When user answers q3 out of order (q1, q2 unanswered), target is first question so walker prompts q1."""
        question_graph = [
            {
                "name": "q1",
                "question": "Q1?",
                "constraints": {"description": "First"},
                "required": True,
            },
            {
                "name": "q2",
                "question": "Q2?",
                "constraints": {"description": "Second"},
                "required": True,
            },
            {
                "name": "q3",
                "question": "Q3?",
                "constraints": {"description": "Third"},
                "required": False,
            },
        ]
        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="ResolveTargetTestAction",
            question_graph=question_graph,
            state=InterviewState.ACTIVE,
        )
        session.responses = {"q3": "out_of_order_answer"}
        await session.save()

        first_node = MagicMock()
        first_node.id = "first_question_node_id"
        q3_node = MagicMock()
        q3_node.id = "q3_node_id"

        action = MagicMock(spec=InterviewInteractAction)
        action._get_first_question_node = AsyncMock(return_value=first_node)
        action._get_question_node = AsyncMock(return_value=q3_node)
        action.get_state_node = AsyncMock(return_value=None)

        # Mock find_next_target to return q1 (unanswered) so we hit SUBMISSION branch
        # instead of "all answered" branch (which would go to REVIEW)
        from jvagent.action.interview.core.graph.question_path_walker import (
            QuestionPathWalker,
        )

        with patch.object(
            QuestionPathWalker,
            "find_next_target",
            new_callable=AsyncMock,
            return_value=first_node,
        ):
            resolver = TargetResolver(action)
            await resolver.resolve(session, Intent.SUBMISSION)

        assert session.target_node == "first_question_node_id"
        action._get_first_question_node.assert_called_once_with(session)
        action._get_question_node.assert_not_called()


class TestTargetResolverCancellationGate:
    """Test that CANCELLATION always routes to CancelledStateNode (intent prioritization)."""

    @pytest.mark.asyncio
    async def test_cancellation_routes_to_cancelled_with_active_task(self, test_db):
        """CANCELLATION routes to CANCELLED when conversation has active task."""
        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=[{"name": "q1", "question": "Q1?", "required": True}],
            state=InterviewState.ACTIVE,
        )

        cancelled_node = MagicMock()
        cancelled_node.id = "cancelled_node_id"

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name.return_value = "TestInterviewAction"
        action.get_state_node = AsyncMock(return_value=cancelled_node)

        visitor = MagicMock()
        visitor.conversation = MagicMock()

        resolver = TargetResolver(action)
        await resolver.resolve(session, Intent.CANCELLATION, visitor=visitor)

        assert session.target_node == "cancelled_node_id"
        action.get_state_node.assert_called_once_with(InterviewState.CANCELLED)

    @pytest.mark.asyncio
    async def test_cancellation_routes_to_cancelled_with_no_active_task(self, test_db):
        """CANCELLATION routes to CANCELLED even when no active task (prioritize intent)."""
        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=[{"name": "q1", "question": "Q1?", "required": True}],
            state=InterviewState.ACTIVE,
        )

        cancelled_node = MagicMock()
        cancelled_node.id = "cancelled_node_id"

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name.return_value = "TestInterviewAction"
        action.get_state_node = AsyncMock(return_value=cancelled_node)

        visitor = MagicMock()
        visitor.conversation = MagicMock()
        visitor.conversation.get_active_tasks.return_value = []

        resolver = TargetResolver(action)
        await resolver.resolve(session, Intent.CANCELLATION, visitor=visitor)

        assert session.target_node == "cancelled_node_id"
        action.get_state_node.assert_called_once_with(InterviewState.CANCELLED)

    @pytest.mark.asyncio
    async def test_cancellation_routes_to_cancelled_with_no_visitor(self, test_db):
        """CANCELLATION routes to CANCELLED even when visitor is None."""
        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=[{"name": "q1", "question": "Q1?", "required": True}],
            state=InterviewState.ACTIVE,
        )

        cancelled_node = MagicMock()
        cancelled_node.id = "cancelled_node_id"

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name.return_value = "TestInterviewAction"
        action.get_state_node = AsyncMock(return_value=cancelled_node)

        resolver = TargetResolver(action)
        await resolver.resolve(session, Intent.CANCELLATION, visitor=None)

        assert session.target_node == "cancelled_node_id"
        action.get_state_node.assert_called_once_with(InterviewState.CANCELLED)

    @pytest.mark.asyncio
    async def test_cancellation_routes_to_cancelled_with_no_conversation(self, test_db):
        """CANCELLATION routes to CANCELLED even when visitor has no conversation."""
        session = await InterviewSession.create(
            agent_id="test_agent",
            conversation_id="test_conv",
            interview_type="TestInterviewAction",
            question_graph=[{"name": "q1", "question": "Q1?", "required": True}],
            state=InterviewState.ACTIVE,
        )

        cancelled_node = MagicMock()
        cancelled_node.id = "cancelled_node_id"

        action = MagicMock(spec=InterviewInteractAction)
        action.get_class_name.return_value = "TestInterviewAction"
        action.get_state_node = AsyncMock(return_value=cancelled_node)

        visitor = MagicMock()
        visitor.conversation = None

        resolver = TargetResolver(action)
        await resolver.resolve(session, Intent.CANCELLATION, visitor=visitor)

        assert session.target_node == "cancelled_node_id"
        action.get_state_node.assert_called_once_with(InterviewState.CANCELLED)
