"""Test to verify pruning of unreachable responses after branch path change.

Uses PostUpdateWalker's _prune_session logic directly by setting the reachable
set to simulate what a graph traversal would compute.
"""

import pytest
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker
from jvagent.action.interview.core.utils.cache_utils import BranchCache
from jvagent.action.interview.core.foundation.enums import InterviewState


@pytest.mark.asyncio
async def test_pruning_removes_unreachable_responses():
    """
    Test that when a branch condition changes, responses from the OLD path are pruned.

    Scenario:
    Q1 (branching point): responses[q1] = "value_a"
    +-- Branch A (if q1=="value_a"): Q2 -> Q3
    +-- Branch B (if q1=="value_b"): Q4 -> Q5

    Setup: User selects Branch A, answers Q2 and Q3
    Update: User changes Q1 to "value_b", which should trigger Branch B
    Expected: Q2 and Q3 responses are pruned, Q1 is updated, Q4/Q5 awaited
    """

    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q1",
            "question": "What is your choice?",
            "constraints": {"type": "string"},
            "branches": [
                {"condition": {"function": "branch_a_condition"}, "target": "q2"},
                {"condition": {"function": "branch_b_condition"}, "target": "q4"},
            ],
            "default_next": "REVIEW",
        },
        {"name": "q2", "question": "Branch A - Q2?", "constraints": {"type": "string"}, "default_next": "q3"},
        {"name": "q3", "question": "Branch A - Q3?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
        {"name": "q4", "question": "Branch B - Q4?", "constraints": {"type": "string"}, "default_next": "q5"},
        {"name": "q5", "question": "Branch B - Q5?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
    ]

    # Setup: User took Branch A path, then updated Q1 to "value_b"
    session.responses = {
        "q1": "value_b",
        "q2": "answer_q2",
        "q3": "answer_q3",
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Simulate PostUpdateWalker traversal result: new path is Q1 -> Q4 -> Q5
    walker = PostUpdateWalker(interview_session=session)
    walker._reachable = {"q1", "q4", "q5"}

    walker._prune_session()

    # Q1 should be preserved (on new path)
    assert "q1" in session.responses, "Q1 should still be answered (the branching point)"
    assert session.responses["q1"] == "value_b", "Q1 should have the new value"

    # Q2 and Q3 should be pruned (old Branch A path)
    assert "q2" not in session.responses, "Q2 response should be pruned (from old path)"
    assert "q3" not in session.responses, "Q3 response should be pruned (from old path)"

    # Q4 and Q5 should not be answered yet (on new path but not answered)
    assert "q4" not in session.responses, "Q4 should not be answered yet"
    assert "q5" not in session.responses, "Q5 should not be answered yet"

    # Pruned responses should be recorded in the audit trail
    pruned = BranchCache(session).get_pruned_responses()
    assert "q2" in pruned, "Q2 should be in pruned audit trail"
    assert "q3" in pruned, "Q3 should be in pruned audit trail"


@pytest.mark.asyncio
async def test_pruning_preserves_pre_branch_answers():
    """
    Ensure questions answered BEFORE the branching point are preserved.

    Graph: q0 -> q1 -> q2(branch) -> [q3_a | q3_b] -> q4

    Scenario:
    1. Answer q0, q1, q2="branch_a", q3_a
    2. Update q2="branch_b" (triggers path change)
    3. Verify q0 and q1 are preserved (pre-branch questions)
    4. Verify q2 is preserved (branching point)
    5. Verify only q3_a is pruned (downstream from branching point)
    """

    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {"name": "q0", "question": "Pre-branch 0?", "constraints": {"type": "string"}, "default_next": "q1"},
        {"name": "q1", "question": "Pre-branch 1?", "constraints": {"type": "string"}, "default_next": "q2"},
        {
            "name": "q2",
            "question": "Branching question?",
            "constraints": {"type": "string"},
            "branches": [
                {"condition": {"function": "branch_a_condition"}, "target": "q3_a"},
                {"condition": {"function": "branch_b_condition"}, "target": "q3_b"},
            ],
            "default_next": "REVIEW",
        },
        {"name": "q3_a", "question": "Branch A?", "constraints": {"type": "string"}, "default_next": "q4"},
        {"name": "q3_b", "question": "Branch B?", "constraints": {"type": "string"}, "default_next": "q4"},
        {"name": "q4", "question": "Post-convergence?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
    ]

    # Setup: User answered pre-branch questions and took Branch A, then updated q2
    session.responses = {
        "q0": "pre_value",
        "q1": "pre_value2",
        "q2": "branch_b",
        "q3_a": "answer_a",
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Simulate PostUpdateWalker traversal: new path is q0 -> q1 -> q2 -> q3_b -> q4
    walker = PostUpdateWalker(interview_session=session)
    walker._reachable = {"q0", "q1", "q2", "q3_b", "q4"}

    walker._prune_session()

    # PRE-BRANCH QUESTIONS SHOULD BE PRESERVED
    assert "q0" in session.responses, "q0 should be preserved (pre-branch)"
    assert session.responses["q0"] == "pre_value"
    assert "q1" in session.responses, "q1 should be preserved (pre-branch)"
    assert session.responses["q1"] == "pre_value2"

    # BRANCHING POINT SHOULD BE PRESERVED
    assert "q2" in session.responses, "q2 should be preserved (branching point)"
    assert session.responses["q2"] == "branch_b"

    # ONLY DOWNSTREAM OLD-BRANCH QUESTION SHOULD BE PRUNED
    assert "q3_a" not in session.responses, "q3_a should be pruned (old branch)"

    # Pruned audit trail
    pruned = BranchCache(session).get_pruned_responses()
    assert "q3_a" in pruned, "q3_a should be in pruned list"
    assert "q0" not in pruned, "q0 should NOT be in pruned list"
    assert "q1" not in pruned, "q1 should NOT be in pruned list"
    assert "q2" not in pruned, "q2 should NOT be in pruned list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
