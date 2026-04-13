import pytest

from jvagent.action.interview.core.graph.question_branch_evaluator import (
    QuestionBranchEvaluator,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.utils.cache_utils import BranchCache


@pytest.mark.asyncio
async def test_operator_condition_reevaluates_on_session_state():
    """Evaluator re-runs each time; changing response changes result."""
    session = InterviewSession()
    session.interview_type = "TestInterview"
    question_name = "q1"
    session.question_graph = [{"name": question_name}]
    session.responses[question_name] = "yes"
    condition = {"op": "equals", "value": "yes"}

    result1 = await QuestionBranchEvaluator.matches(
        condition, session, implicit_question=question_name, visitor=object()
    )
    assert result1 is True

    session.responses[question_name] = "no"
    result2 = await QuestionBranchEvaluator.matches(
        condition, session, implicit_question=question_name, visitor=object()
    )
    assert result2 is False


@pytest.mark.asyncio
async def test_branch_cache_invalidate_clears_entry():
    """BranchCache invalidate(question_name) clears that question's cached target."""
    session = InterviewSession()
    session.context = {}
    branch_cache = BranchCache(session)
    branch_cache.set("a", "target_a")
    assert branch_cache.get("a") == "target_a"
    branch_cache.invalidate("a")
    assert branch_cache.get("a") is None
