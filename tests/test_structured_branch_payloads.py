"""Test structured branch-change payloads from PruningService."""

import pytest
from jvagent.action.interview.core.session.pruning_service import PruningService
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.state.state_handlers import StateHandler
from unittest.mock import Mock


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

    payload = PruningService.compose_branch_change_payload(
        change_details=change_details,
        session=session,
        question_walker=mock_walker
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


def test_state_handlers_format_directives():
    """Test state handlers format payloads into user-facing text."""
    payload = {
        "type": "branch_change",
        "branching_question": "housing_type",
        "new_target": "rural_questions",
        "pruned_questions": ["urban_density"],
        "metadata": {
            "updated_field_display": "Housing Type",
            "pruned_count": 1
        }
    }

    # Create a minimal StateHandler (we only need the formatting method)
    mock_action = Mock()
    handler = StateHandler(mock_action)

    # Test ACTIVE state formatting
    directive_text = handler._format_branch_change_directive(payload)

    # Verify formatting
    assert isinstance(directive_text, str), "Directive should be a string"
    assert "Updated Housing Type" in directive_text, "Should mention updated field"
    assert "cleared 1 previous answer" in directive_text, "Should mention pruned count"
    assert "urban_density" in directive_text, "Should mention pruned question"

    print("✓ Directive formatting test passed")


def test_format_pruned_notification():
    """Test REVIEW state pruned notification formatting."""
    # Test with 1 pruned question
    payload_single = {
        "pruned_questions": ["question_a"],
        "metadata": {"pruned_count": 1}
    }

    mock_action = Mock()
    handler = StateHandler(mock_action)

    notification = handler._format_pruned_notification(payload_single)
    assert "removed 1 previous answer" in notification, "Should mention single answer"
    assert "question_a" in notification, "Should mention the question"

    # Test with multiple pruned questions
    payload_multiple = {
        "pruned_questions": ["question_a", "question_b", "question_c"],
        "metadata": {"pruned_count": 3}
    }

    notification = handler._format_pruned_notification(payload_multiple)
    assert "removed 3 previous answers" in notification, "Should mention multiple answers"
    assert "question_a" in notification, "Should list questions"
    assert "question_b" in notification, "Should list questions"
    assert "question_c" in notification, "Should list questions"

    # Test with no pruned questions
    payload_none = {
        "pruned_questions": [],
        "metadata": {"pruned_count": 0}
    }

    notification = handler._format_pruned_notification(payload_none)
    assert notification == "", "Should return empty string if nothing pruned"

    print("✓ Pruned notification formatting test passed")


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

    payload = PruningService.compose_branch_change_payload(
        change_details=change_details_question,
        session=session,
        question_walker=mock_walker
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

    payload = PruningService.compose_branch_change_payload(
        change_details=change_details_state,
        session=session,
        question_walker=mock_walker
    )

    assert payload["is_state_target"] is True, "Should identify state target"

    print("✓ State target identification test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
