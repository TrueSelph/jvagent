"""Comprehensive test for branch re-routing and pruning behavior.

Uses QuestionPathWalker's _prune_session logic to verify that old-path
responses are removed and new-path responses are left untouched.
"""

import pytest
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_path_walker import QuestionPathWalker
from jvagent.action.interview.core.utils.cache_utils import BranchCache
from jvagent.action.interview.core.foundation.enums import InterviewState


@pytest.mark.asyncio
async def test_branch_change_triggers_pruning():
    """
    Comprehensive test of the pruning fix.

    Tests the exact scenario:
    1. User answers questions on Branch A (urban) path
    2. User updates location to "rural" (Branch B)
    3. QuestionPathWalker.sync computes new reachable set: {location, rural_details}
    4. Old-path response (urban_details) is pruned
    """

    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "location",
            "question": "Is this urban or rural?",
            "constraints": {"type": "string"},
            "branches": [
                {"condition": {"function": "is_urban"}, "target": "urban_details"},
                {"condition": {"function": "is_rural"}, "target": "rural_details"},
            ],
            "default_next": "REVIEW",
        },
        {"name": "urban_details", "question": "Urban area name and density?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
        {"name": "rural_details", "question": "Rural area name and population?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
    ]

    # Initial state: User chose "urban", then updated to "rural"
    session.responses = {
        "location": "rural",
        "urban_details": "Downtown, high density",
    }
    session.state = InterviewState.ACTIVE
    await session.save()

    # Verify initial state
    assert "location" in session.responses
    assert "urban_details" in session.responses
    assert "rural_details" not in session.responses

    # Simulate QuestionPathWalker traversal result: new path is location -> rural_details
    walker = QuestionPathWalker(interview_session=session)
    walker._reachable = {"location", "rural_details"}

    walker._prune_session()

    # Verify pruning
    assert "location" in session.responses, "Location should still be answered"
    assert session.responses["location"] == "rural", "Location should have new value"
    assert "urban_details" not in session.responses, "Urban details should be pruned"
    assert "rural_details" not in session.responses, "Rural details not answered yet"

    # Verify pruned audit trail
    pruned = BranchCache(session).get_pruned_responses()
    assert "urban_details" in pruned, "urban_details should be in pruned trail"
    assert pruned["urban_details"]["value"] == "Downtown, high density"


@pytest.mark.asyncio
async def test_empty_reachable_set_does_not_prune():
    """Safety guard: if reachable is empty (traversal failed), nothing is pruned."""

    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {"name": "q1", "question": "Q1?", "constraints": {"type": "string"}, "default_next": "REVIEW"},
    ]
    session.responses = {"q1": "answer"}
    session.state = InterviewState.ACTIVE
    await session.save()

    walker = QuestionPathWalker(interview_session=session)
    # _reachable is empty (default) — simulates a failed traversal
    walker._prune_session()

    assert "q1" in session.responses, "No responses should be pruned when reachable is empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
