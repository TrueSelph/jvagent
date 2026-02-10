import pytest

from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.utils.cache_utils import BranchCache


@pytest.mark.asyncio
async def test_path_change_prunes_unreachable_responses():
    session = InterviewSession()
    session.interview_type = "TestInterview"

    # Build question graph: qA branches to qB or qC, both lead to qD (converge)
    session.question_graph = [
        {"name": "qA", "branches": [
            {"condition": {"op": "equals", "value": "b"}, "target": "qB"},
            {"condition": {"op": "equals", "value": "c"}, "target": "qC"}
        ], "default_next": "qD"},
        {"name": "qB", "default_next": "qD"},
        {"name": "qC", "default_next": "qD"},
        {"name": "qD", "default_next": "REVIEW"}
    ]

    # Initial answers follow path qA->qB->qD
    session.responses = {"qA": "b", "qB": "valB", "qD": "valD"}

    # Record previous branch path as qA -> qB
    branch_cache = BranchCache(session)
    branch_cache.record_branch_path("qA", 0, "qB", False)

    # Now update qA to take qC path
    session.responses["qA"] = "c"

    # Run detection/prune
    walker = QuestionWalker()
    changed = await walker.detect_and_prune_altered_path(session, "qA", interview_action=None, visitor=object())
    assert changed is True

    # qB should be pruned, qD should remain (converged)
    assert "qB" not in session.responses
    assert "qD" in session.responses
