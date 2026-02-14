"""Test that pruning correctly handles convergence: responses after a convergence
point are preserved even when the branch before the convergence changes.

Uses QuestionPathWalker's _prune_session directly.
"""

import pytest
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_path_walker import QuestionPathWalker
from jvagent.action.interview.core.utils.cache_utils import BranchCache


@pytest.mark.asyncio
async def test_path_change_prunes_unreachable_responses():
    """qA branches to qB or qC, both converge at qD.

    Initial path: qA -> qB -> qD
    Updated path: qA -> qC -> qD
    Expected: qB pruned, qD preserved (after convergence).
    """
    session = InterviewSession()
    session.interview_type = "TestInterview"

    session.question_graph = [
        {"name": "qA", "branches": [
            {"condition": {"op": "equals", "value": "b"}, "target": "qB"},
            {"condition": {"op": "equals", "value": "c"}, "target": "qC"},
        ], "default_next": "qD"},
        {"name": "qB", "default_next": "qD"},
        {"name": "qC", "default_next": "qD"},
        {"name": "qD", "default_next": "REVIEW"},
    ]

    # User updated qA from "b" to "c"
    session.responses = {"qA": "c", "qB": "valB", "qD": "valD"}
    await session.save()

    # Simulate QuestionPathWalker traversal: new path is qA -> qC -> qD
    walker = QuestionPathWalker(interview_session=session)
    walker._reachable = {"qA", "qC", "qD"}

    walker._prune_session()

    # qB should be pruned (old branch), qD preserved (convergence point)
    assert "qB" not in session.responses, "qB should be pruned"
    assert "qD" in session.responses, "qD should remain (convergence)"
    assert session.responses["qD"] == "valD"

    # qA preserved
    assert "qA" in session.responses
    assert session.responses["qA"] == "c"

    # Audit trail
    pruned = BranchCache(session).get_pruned_responses()
    assert "qB" in pruned


@pytest.mark.asyncio
async def test_update_queue_pruned_for_unreachable():
    """Entries in update_queue for unreachable questions should be removed."""
    session = InterviewSession()
    session.interview_type = "TestInterview"
    session.question_graph = [
        {"name": "qA", "default_next": "qB"},
        {"name": "qB", "default_next": "REVIEW"},
    ]
    session.responses = {"qA": "a"}
    session.update_queue = [
        {"field": "qA", "value": "a2", "old_value": "a"},
        {"field": "qX", "value": "x", "old_value": None},  # unreachable
    ]
    await session.save()

    walker = QuestionPathWalker(interview_session=session)
    walker._reachable = {"qA", "qB"}

    walker._prune_session()

    remaining_fields = [e["field"] for e in session.update_queue]
    assert "qA" in remaining_fields
    assert "qX" not in remaining_fields, "Unreachable update_queue entry should be removed"
