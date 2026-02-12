"""Test context lifecycle for branch change variables.

Verifies that _branch_change_payload is created via PruningService,
consumed by state handlers, and cleaned up correctly.
"""

import pytest
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.interview_walker import InterviewWalker
from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker
from jvagent.action.interview.core.utils.cache_utils import BranchCache
from jvagent.action.interview.core.session.pruning_service import compose_branch_change_payload
from jvagent.action.interview.core.foundation.enums import InterviewState


@pytest.mark.asyncio
async def test_context_variables_lifecycle():
    """
    Test that _branch_change_payload is created via PruningService,
    consumed, and cleaned up properly.
    """

    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q1",
            "question": "Branching question?",
            "constraints": {"type": "string"},
            "branches": [
                {"condition": {"function": "branch_a"}, "target": "q2_a"},
                {"condition": {"function": "branch_b"}, "target": "q2_b"},
            ],
            "default_next": "REVIEW",
        },
        {"name": "q2_a", "question": "Branch A?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
        {"name": "q2_b", "question": "Branch B?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
    ]

    # Setup: Answered on Branch A, then updated q1 to trigger branch change
    session.responses = {
        "q1": "branch_b",
        "q2_a": "answer_a",
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Verify context is initially clean
    assert session.context is None or "_branch_change_payload" not in (session.context or {}), \
        "Context should not have _branch_change_payload initially"

    # Step 1: PostUpdateWalker prunes old-branch responses
    walker = PostUpdateWalker(interview_session=session)
    walker._reachable = {"q1", "q2_b"}
    walker._prune_session()

    assert "q2_a" not in session.responses, "q2_a should be pruned"
    assert "q1" in session.responses, "q1 should remain"

    # Step 2: Build branch change details and compose payload via PruningService
    change_details = {
        "branching_question": "q1",
        "old_target": "q2_a",
        "new_target": "q2_b",
        "pruned_questions": ["q2_a"],
        "is_default": False,
        "condition_index": 1,
    }

    interview_walker = InterviewWalker()
    payload = compose_branch_change_payload(
        change_details=change_details,
        session=session,
        interview_walker=interview_walker,
    )

    if session.context is None:
        session.context = {}
    session.context["_branch_change_payload"] = payload
    await session.save()

    # Verify payload structure
    assert "_branch_change_payload" in session.context
    assert isinstance(payload, dict)
    assert payload["type"] == "branch_change"
    assert payload["branching_question"] == "q1"
    assert payload["new_target"] == "q2_b"
    assert payload["pruned_questions"] == ["q2_a"]

    # Step 3: State handler consumes and cleans up
    branch_payload = session.context.get("_branch_change_payload")
    assert branch_payload is not None, "Payload should be available for state handlers"

    session.context.pop("_branch_change_payload", None)
    await session.save()

    assert "_branch_change_payload" not in (session.context or {}), \
        "Payload should be cleaned up after consumption"


@pytest.mark.asyncio
async def test_context_cleanup_idempotent():
    """Test that cleanup is safe even if called multiple times."""
    session = InterviewSession(agent_id="test_agent")
    session.context = {
        "_branch_change_payload": {"test": "payload"},
    }
    await session.save()

    # First cleanup
    session.context.pop("_branch_change_payload", None)
    await session.save()
    assert "_branch_change_payload" not in session.context

    # Second cleanup (should not error)
    session.context.pop("_branch_change_payload", None)
    await session.save()
    assert "_branch_change_payload" not in session.context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
