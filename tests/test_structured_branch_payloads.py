"""Test structured branch-change payloads from PruningService."""

import pytest
from unittest.mock import Mock

from jvagent.action.interview.core.session.pruning_service import compose_branch_change_payload
from jvagent.action.interview.core.session.interview_session import InterviewSession


@pytest.mark.asyncio
async def test_pruning_service_returns_structured_payload():
    """Test PruningService returns dict, not formatted string."""
    change_details = {
        "branching_question": "q2",
        "old_target": "q3_a",
        "new_target": "q3_b",
        "pruned_questions": ["q3_a", "q4"],
        "is_default": False,
        "condition_index": 1
    }

    # Create minimal session
    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "q2",
            "question": "Test question?",
            "constraints": {"type": "string"}
        }
    ]
    await session.save()

    # Create mock question walker
    mock_walker = Mock()
    mock_walker._is_state_target = Mock(return_value=False)

    payload = compose_branch_change_payload(
        change_details=change_details,
        session=session,
        interview_walker=mock_walker
    )

    # Verify structure
    assert isinstance(payload, dict), "Payload should be a dict"
    assert payload["type"] == "branch_change", "Type should be 'branch_change'"
    assert payload["branching_question"] == "q2", "Should have branching_question"
    assert payload["new_target"] == "q3_b", "Should have new_target"
    assert payload["old_target"] == "q3_a", "Should have old_target"
    assert payload["is_state_target"] is False, "Should have is_state_target"
    assert "metadata" in payload, "Should have metadata"
    assert "pruned_count" in payload["metadata"], "Metadata should have pruned_count"
    assert payload["metadata"]["pruned_count"] == 2, "Should count pruned questions"
    assert payload["pruned_questions"] == ["q3_a", "q4"], "Should include pruned questions"

    # Verify NOT a string
    assert not isinstance(payload, str), "Payload should NOT be a string"

    print("✓ Payload structure test passed")


def test_payload_includes_state_target_info():
    """Test payload correctly identifies state vs question targets."""
    # Test with question target
    change_details_question = {
        "branching_question": "q1",
        "old_target": "q2",
        "new_target": "q3",
        "pruned_questions": [],
        "is_default": False,
        "condition_index": 0
    }

    session = Mock()
    mock_walker = Mock()
    mock_walker._is_state_target = Mock(return_value=False)

    payload = compose_branch_change_payload(
        change_details=change_details_question,
        session=session,
        interview_walker=mock_walker
    )

    assert payload["is_state_target"] is False, "Should identify question target"

    # Test with state target
    change_details_state = {
        "branching_question": "q1",
        "old_target": "q2",
        "new_target": "COMPLETED",
        "pruned_questions": [],
        "is_default": False,
        "condition_index": 0
    }

    mock_walker._is_state_target = Mock(return_value=True)

    payload = compose_branch_change_payload(
        change_details=change_details_state,
        session=session,
        interview_walker=mock_walker
    )

    assert payload["is_state_target"] is True, "Should identify state target"

    print("✓ State target identification test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
