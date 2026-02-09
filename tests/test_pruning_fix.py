"""Test to verify pruning of unreachable responses after branch path change."""

import pytest
from unittest.mock import AsyncMock, patch
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.foundation.enums import InterviewState


@pytest.mark.asyncio
async def test_pruning_removes_unreachable_responses():
    """
    Test that when a branch condition changes, responses from the OLD path are pruned.
    
    Scenario:
    Q1 (branching point): responses[q1] = "value_a"
    ├─ Branch A (if q1=="value_a"): Q2 → Q3
    └─ Branch B (if q1=="value_b"): Q4 → Q5
    
    Setup: User selects Branch A, answers Q2 and Q3
    Update: User changes Q1 to "value_b", which should trigger Branch B
    Expected: Q2 and Q3 responses are pruned, Q1 is updated, Q4/Q5 awaited
    """
    
    # Create session with branching graph
    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q1",
            "question": "What is your choice?",
            "constraints": {"type": "string"},
            "branches": [
                {
                    "condition": {"function": "branch_a_condition"},
                    "target": "q2"
                },
                {
                    "condition": {"function": "branch_b_condition"},
                    "target": "q4"
                }
            ],
            "default_next": "REVIEW",
        },
        {
            "name": "q2",
            "question": "Branch A - Q2?",
            "constraints": {"type": "string"},
            "default_next": "q3",
        },
        {
            "name": "q3",
            "question": "Branch A - Q3?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
        {
            "name": "q4",
            "question": "Branch B - Q4?",
            "constraints": {"type": "string"},
            "default_next": "q5",
        },
        {
            "name": "q5",
            "question": "Branch B - Q5?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
    ]
    
    # Setup: User took Branch A path
    session.responses = {
        "q1": "value_a",  # Condition triggers Branch A
        "q2": "answer_q2",
        "q3": "answer_q3",
    }
    session.state = InterviewState.ACTIVE
    await session.save()
    
    # Create walker to test reachability
    walker = QuestionWalker()
    walker.interview_session = session
    
    # Mock the branch evaluator to check conditions based on actual responses
    with patch.object(QuestionBranchEvaluator, 'matches', new_callable=AsyncMock) as mock_matches:
        async def condition_evaluator(condition, session, implicit_question=None, visitor=None):
            """Evaluate conditions based on current session responses."""
            func_name = condition.get("function")
            if func_name == "branch_a_condition":
                return session.responses.get("q1") == "value_a"
            elif func_name == "branch_b_condition":
                return session.responses.get("q1") == "value_b"
            return False
        
        mock_matches.side_effect = condition_evaluator
        
        # Simulate: User updates Q1 to "value_b" (should trigger Branch B)
        old_value = session.get_response("q1")
        session.update_response("q1", "value_b", old_value)
        await session.save()
        
        # Now the new path should be Branch B (Q4 → Q5), not Branch A (Q2 → Q3)
        # Get the reachable questions on the NEW path
        new_path_questions = await walker._get_reachable_questions(
            session, "q1", "q4"
        )
        
        # The new path should include: q1, q4, q5 (and nothing from branch A)
        assert "q1" in new_path_questions, "Branching point should be in reachable"
        assert "q4" in new_path_questions, "Q4 (start of new branch) should be reachable"
        assert "q5" in new_path_questions, "Q5 (next after Q4) should be reachable"
        
        # Q2 and Q3 should NOT be in new path (they're from old Branch A)
        assert "q2" not in new_path_questions, "Q2 (from old branch A) should NOT be reachable"
        assert "q3" not in new_path_questions, "Q3 (from old branch A) should NOT be reachable"
        
        # Now test the actual pruning via detect_and_prune_altered_path
        # First, we need to set up the branch cache with the old path
        from jvagent.action.interview.core.utils.cache_utils import BranchFunctionCache
        cache = BranchFunctionCache(session)
        # Record the old path so detect_and_prune can compare
        cache.record_branch_path("q1", 0, "q2", False)  # Branch index 0, target q2
        await session.save()
        
        path_changed = await walker.detect_and_prune_altered_path(
            session, "q1", interview_action=None, visitor=None
        )
        
        # Path should have changed
        assert path_changed is True, "Path change should have been detected"
        
        # Responses should now be pruned
        assert "q1" in session.responses, "Q1 should still be answered (the branching point)"
        assert session.responses["q1"] == "value_b", "Q1 should have the new value"
        assert "q2" not in session.responses, "Q2 response should be pruned (from old path)"
        assert "q3" not in session.responses, "Q3 response should be pruned (from old path)"
        
        # Q4 and Q5 should not be answered yet (they're on new path but not answered)
        assert "q4" not in session.responses, "Q4 should not be answered yet (on new path)"
        assert "q5" not in session.responses, "Q5 should not be answered yet (on new path)"
        
        # Branch change details should be captured
        branch_change = session.context.get("_branch_change_details", {})
        assert branch_change is not None, "Branch change details should be recorded"
        assert "q2" in branch_change.get("pruned_questions", []), "Q2 should be in pruned list"
        assert "q3" in branch_change.get("pruned_questions", []), "Q3 should be in pruned list"
        
        print("✓ Pruning test passed: Old path responses properly removed")


@pytest.mark.asyncio
async def test_pruning_preserves_pre_branch_answers():
    """
    Ensure questions answered BEFORE the branching point are preserved.

    Graph: q0 → q1 → q2(branch) → [q3_a | q3_b] → q4

    Scenario:
    1. Answer q0="pre_value", q1="pre_value2", q2="branch_a", q3_a="value_a"
    2. Update q2="branch_b" (triggers path change)
    3. Verify q0 and q1 are preserved (pre-branch questions)
    4. Verify q2 is preserved (branching point)
    5. Verify only q3_a is pruned (downstream from branching point)
    """
    # Create session with pre-branch questions
    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q0",
            "question": "Pre-branch question 0?",
            "constraints": {"type": "string"},
            "default_next": "q1",
        },
        {
            "name": "q1",
            "question": "Pre-branch question 1?",
            "constraints": {"type": "string"},
            "default_next": "q2",
        },
        {
            "name": "q2",
            "question": "Branching question?",
            "constraints": {"type": "string"},
            "branches": [
                {
                    "condition": {"function": "branch_a_condition"},
                    "target": "q3_a"
                },
                {
                    "condition": {"function": "branch_b_condition"},
                    "target": "q3_b"
                }
            ],
            "default_next": "REVIEW",
        },
        {
            "name": "q3_a",
            "question": "Branch A question?",
            "constraints": {"type": "string"},
            "default_next": "q4",
        },
        {
            "name": "q3_b",
            "question": "Branch B question?",
            "constraints": {"type": "string"},
            "default_next": "q4",
        },
        {
            "name": "q4",
            "question": "Post-convergence question?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
    ]

    # Setup: User answered pre-branch questions and took Branch A
    session.responses = {
        "q0": "pre_value",       # Pre-branch question
        "q1": "pre_value2",      # Pre-branch question
        "q2": "branch_a",        # Branching point
        "q3_a": "answer_a",      # Branch A downstream question
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Create walker
    walker = QuestionWalker()
    walker.interview_session = session

    # Mock the branch evaluator
    with patch.object(QuestionBranchEvaluator, 'matches', new_callable=AsyncMock) as mock_matches:
        async def condition_evaluator(condition, session, implicit_question=None, visitor=None):
            """Evaluate conditions based on current session responses."""
            func_name = condition.get("function")
            if func_name == "branch_a_condition":
                return session.responses.get("q2") == "branch_a"
            elif func_name == "branch_b_condition":
                return session.responses.get("q2") == "branch_b"
            return False

        mock_matches.side_effect = condition_evaluator

        # Simulate: User updates q2 to "branch_b" (should trigger Branch B)
        old_value = session.get_response("q2")
        session.update_response("q2", "branch_b", old_value)
        await session.save()

        # Set up branch cache with old path
        from jvagent.action.interview.core.utils.cache_utils import BranchFunctionCache
        cache = BranchFunctionCache(session)
        cache.record_branch_path("q2", 0, "q3_a", False)
        await session.save()

        # Detect and prune altered path
        path_changed = await walker.detect_and_prune_altered_path(
            session, "q2", interview_action=None, visitor=None
        )

        # Path should have changed
        assert path_changed is True, "Path change should have been detected"

        # PRE-BRANCH QUESTIONS SHOULD BE PRESERVED
        assert "q0" in session.responses, "q0 should be preserved (pre-branch question)"
        assert session.responses["q0"] == "pre_value", "q0 value should be unchanged"
        assert "q1" in session.responses, "q1 should be preserved (pre-branch question)"
        assert session.responses["q1"] == "pre_value2", "q1 value should be unchanged"

        # BRANCHING POINT SHOULD BE PRESERVED
        assert "q2" in session.responses, "q2 should be preserved (branching point)"
        assert session.responses["q2"] == "branch_b", "q2 should have the new value"

        # ONLY DOWNSTREAM QUESTIONS SHOULD BE PRUNED
        assert "q3_a" not in session.responses, "q3_a should be pruned (downstream from branching point)"

        # Branch change details should show only downstream questions pruned
        branch_change = session.context.get("_branch_change_details", {})
        assert branch_change is not None, "Branch change details should be recorded"
        pruned = branch_change.get("pruned_questions", [])
        assert "q3_a" in pruned, "q3_a should be in pruned list"
        assert "q0" not in pruned, "q0 should NOT be in pruned list (pre-branch)"
        assert "q1" not in pruned, "q1 should NOT be in pruned list (pre-branch)"
        assert "q2" not in pruned, "q2 should NOT be in pruned list (branching point)"

        print("✓ Pre-branch preservation test passed: Pre-branch answers properly preserved")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
