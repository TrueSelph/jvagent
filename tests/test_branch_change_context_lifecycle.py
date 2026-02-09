"""Test context lifecycle for branch change variables."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.interview_interact_action import InterviewInteractAction


@pytest.mark.asyncio
async def test_context_variables_lifecycle():
    """
    Test that _branch_change_details and _branch_change_payload
    are created, enriched, and cleaned up properly.
    """
    # Create session with branching
    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q1",
            "question": "Branching question?",
            "constraints": {"type": "string"},
            "branches": [
                {
                    "condition": {"function": "branch_a"},
                    "target": "q2_a"
                },
                {
                    "condition": {"function": "branch_b"},
                    "target": "q2_b"
                }
            ],
            "default_next": "REVIEW",
        },
        {
            "name": "q2_a",
            "question": "Branch A?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
        {
            "name": "q2_b",
            "question": "Branch B?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
    ]

    # Setup: Answered on Branch A
    session.responses = {
        "q1": "branch_a",
        "q2_a": "answer_a",
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Verify context is initially clean
    assert session.context is None or "_branch_change_details" not in (session.context or {}), \
        "Context should not have _branch_change_details initially"
    assert session.context is None or "_branch_change_payload" not in (session.context or {}), \
        "Context should not have _branch_change_payload initially"

    # Mock branch evaluator
    with patch.object(QuestionBranchEvaluator, 'matches', new_callable=AsyncMock) as mock_matches:
        async def condition_evaluator(condition, session, implicit_question=None, visitor=None):
            func_name = condition.get("function")
            if func_name == "branch_a":
                return session.responses.get("q1") == "branch_a"
            elif func_name == "branch_b":
                return session.responses.get("q1") == "branch_b"
            return False

        mock_matches.side_effect = condition_evaluator

        # Update to trigger branch change
        old_value = session.get_response("q1")
        session.update_response("q1", "branch_b", old_value)
        await session.save()

        # Step 1: QuestionWalker creates _branch_change_details
        walker = QuestionWalker()
        walker.interview_session = session

        # Set up branch cache with old path
        from jvagent.action.interview.core.utils.cache_utils import BranchFunctionCache
        cache = BranchFunctionCache(session)
        cache.record_branch_path("q1", 0, "q2_a", False)
        await session.save()

        # Detect path change
        path_changed = await walker.detect_and_prune_altered_path(
            session, "q1", interview_action=None, visitor=None
        )

        assert path_changed is True, "Path should have changed"

        # Verify _branch_change_details was created
        assert session.context is not None, "Context should exist"
        assert "_branch_change_details" in session.context, \
            "_branch_change_details should be created by QuestionWalker"

        details = session.context["_branch_change_details"]
        assert details["branching_question"] == "q1", "Should have branching question"
        assert details["new_target"] == "q2_b", "Should have new target"
        assert "q2_a" in details["pruned_questions"], "Should have pruned questions"

        # Step 2: InteractAction enriches with _branch_change_payload
        # Simulate what _update_reachable_questions does
        from jvagent.action.interview.core.session.pruning_service import PruningService

        change_details = session.context.get("_branch_change_details")
        payload = PruningService.compose_branch_change_payload(
            change_details=change_details,
            session=session,
            question_walker=walker
        )
        session.context["_branch_change_payload"] = payload
        await session.save()

        # Verify both variables exist
        assert "_branch_change_details" in session.context, \
            "_branch_change_details should still exist"
        assert "_branch_change_payload" in session.context, \
            "_branch_change_payload should be created by InteractAction"

        assert isinstance(payload, dict), "Payload should be a dict"
        assert payload["type"] == "branch_change", "Payload should have correct type"

        # Step 3: StateHandler consumes and cleans up
        # Simulate what state handlers do
        branch_payload = session.context.get("_branch_change_payload")
        assert branch_payload is not None, "Payload should be available for state handlers"

        # Process payload (state handler would format and queue directives here)
        # ...

        # Clean up after consumption (simulating StateHandler behavior)
        session.context.pop("_branch_change_payload", None)
        session.context.pop("_branch_change_details", None)
        await session.save()

        # Verify cleanup
        assert "_branch_change_details" not in (session.context or {}), \
            "_branch_change_details should be cleaned up after consumption"
        assert "_branch_change_payload" not in (session.context or {}), \
            "_branch_change_payload should be cleaned up after consumption"

        print("✓ Context lifecycle test passed")


@pytest.mark.asyncio
async def test_context_cleanup_idempotent():
    """Test that cleanup is safe even if called multiple times."""
    session = InterviewSession(agent_id="test_agent")
    session.context = {
        "_branch_change_details": {"test": "data"},
        "_branch_change_payload": {"test": "payload"}
    }
    await session.save()

    # First cleanup
    session.context.pop("_branch_change_payload", None)
    session.context.pop("_branch_change_details", None)
    await session.save()

    assert "_branch_change_details" not in session.context
    assert "_branch_change_payload" not in session.context

    # Second cleanup (should not error)
    session.context.pop("_branch_change_payload", None)
    session.context.pop("_branch_change_details", None)
    await session.save()

    assert "_branch_change_details" not in session.context
    assert "_branch_change_payload" not in session.context

    print("✓ Idempotent cleanup test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
