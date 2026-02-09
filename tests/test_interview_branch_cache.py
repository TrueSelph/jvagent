import pytest

from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.utils.cache_utils import BranchFunctionCache


@pytest.mark.asyncio
async def test_operator_condition_cache_hit_and_invalidate():
    session = InterviewSession()
    session.interview_type = "TestInterview"
    question_name = "q1"
    session.question_graph = [{"name": question_name}]

    # initial value that matches condition
    session.responses[question_name] = "yes"

    condition = {"op": "equals", "value": "yes"}

    # First evaluation should be True and populate cache
    result1 = await QuestionBranchEvaluator.matches(condition, session, implicit_question=question_name, visitor=object())
    assert result1 is True

    # Ensure cache entry exists
    branch_cache = BranchFunctionCache(session)
    cache_key = branch_cache._make_cache_key(question_name, condition)
    entry = branch_cache.get(cache_key)
    assert entry is not None

    # Change the response so dependency snapshot differs
    session.responses[question_name] = "no"

    # Second evaluation should be False (cache invalidated due to changed dependency)
    result2 = await QuestionBranchEvaluator.matches(condition, session, implicit_question=question_name, visitor=object())
    assert result2 is False


@pytest.mark.asyncio
async def test_function_condition_cache_dependency_tracking():
    # Basic test ensuring branch function caching records dependencies and invalidates
    session = InterviewSession()
    session.interview_type = "TestInterview"
    # create minimal graph
    session.question_graph = [{"name": "a"}, {"name": "b"}]

    # Create a branch function in-place that reads session.responses['a']
    async def sample_func(s, visitor):
        return s.responses.get('a', 0)

    # Use BranchFunctionCache to set a cached value and then check invalidate_by_response
    branch_cache = BranchFunctionCache(session)
    cache_key = branch_cache._make_cache_key('a', {"function": "sample_func"}, 'sample_func')

    # Set dependency value snapshot
    session.responses['a'] = 5
    branch_cache.set(cache_key, 5, {'a'})
    entry = branch_cache.get(cache_key)
    assert entry is not None
    assert entry.get('result') == 5

    # Change dependency
    session.responses['a'] = 7
    entry2 = branch_cache.get(cache_key)
    assert entry2 is None
