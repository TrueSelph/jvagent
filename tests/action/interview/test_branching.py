"""Tests for conditional branching logic."""

import pytest
from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator


@pytest.fixture
async def test_session_with_branches(test_db):
    """Create a test session with conditional branches."""
    question_index = [
        {
            "name": "user_type",
            "question": "Are you a premium or standard user?",
            "constraints": {"description": "User account type", "type": "string"},
            "required": True,
            "branches": [
                {
                    "condition": {"op": "equals", "value": "premium"},
                    "target": "premium_features"
                },
                {
                    "condition": {"op": "equals", "value": "standard"},
                    "target": "standard_setup"
                }
            ],
            "default_next": "contact_info"
        },
        {
            "name": "premium_features",
            "question": "Which premium features interest you?",
            "constraints": {"description": "Premium features", "type": "string"},
            "required": False,
            "default_next": "contact_info"
        },
        {
            "name": "standard_setup",
            "question": "Standard setup question",
            "constraints": {"description": "Standard setup", "type": "string"},
            "required": False,
            "default_next": "contact_info"
        },
        {
            "name": "contact_info",
            "question": "What's your contact information?",
            "constraints": {"description": "Contact info", "type": "string"},
            "required": True
        }
    ]
    
    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_index=question_index,
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
            condition,
            session,
            implicit_question="user_type"
        )
        assert matches is True
        
        # Test non-matching value
        session.set_response("user_type", "standard")
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="user_type"
        )
        assert matches is False
    
    @pytest.mark.asyncio
    async def test_branch_in_condition(self, test_session_with_branches):
        """Test 'in' condition matching."""
        session = test_session_with_branches
        
        session.set_response("user_type", "premium")
        
        condition = {"op": "in", "value": ["premium", "vip"]}
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="user_type"
        )
        assert matches is True
        
        # Test value not in list
        session.set_response("user_type", "standard")
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="user_type"
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
            condition,
            session,
            implicit_question="user_type"
        )
        assert matches is True
        
        # Test exists when value is None
        session.responses.pop("user_type", None)
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="user_type"
        )
        assert matches is False
    
    @pytest.mark.asyncio
    async def test_branch_comparison_operators(self, test_session_with_branches):
        """Test comparison operators (>=, <=, >, <)."""
        session = test_session_with_branches
        
        # Add numeric question
        session.question_graph.append({
            "name": "age",
            "question": "What's your age?",
            "constraints": {"description": "Age", "type": "integer"},
            "required": False
        })
        
        # Test >=
        session.set_response("age", 25)
        condition = {"op": ">=", "value": 18}
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="age"
        )
        assert matches is True
        
        # Test <
        session.set_response("age", 15)
        condition = {"op": "<", "value": 18}
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="age"
        )
        assert matches is True
    
    @pytest.mark.asyncio
    async def test_branch_with_unanswered_question(self, test_session_with_branches):
        """Test branch evaluation when question is not answered."""
        session = test_session_with_branches
        
        # Don't set user_type response
        condition = {"op": "equals", "value": "premium"}
        matches = await QuestionBranchEvaluator.matches(
            condition,
            session,
            implicit_question="user_type"
        )
        # Should return False when question is not answered
        assert matches is False
    
    @pytest.mark.asyncio
    async def test_get_next_questions_with_branches(self, test_session_with_branches):
        """Test getting next questions based on branch conditions."""
        session = test_session_with_branches
        
        # Set premium response
        session.set_response("user_type", "premium")
        
        next_questions = await session.get_next_questions("user_type")
        assert "premium_features" in next_questions
        
        # Set standard response
        session.set_response("user_type", "standard")
        next_questions = await session.get_next_questions("user_type")
        assert "standard_setup" in next_questions
