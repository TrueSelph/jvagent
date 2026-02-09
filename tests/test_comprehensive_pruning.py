"""Comprehensive test for branch re-routing and pruning behavior."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_walker import QuestionWalker
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.foundation.enums import InterviewState


@pytest.mark.asyncio
async def test_branch_change_triggers_pruning():
    """
    Comprehensive test of the pruning fix.
    
    Tests the exact scenario:
    1. User answers questions on Branch A path
    2. User updates a branching question response
    3. New branch condition evaluates to Branch B
    4. Old path responses (from Branch A) are pruned
    5. New path is traversed (Branch B questions available)
    """
    
    # Create interview session
    session = InterviewSession(agent_id="test_agent")
    session.question_graph = [
        {
            "name": "location",
            "question": "Is this urban or rural?",
            "constraints": {"type": "string"},
            "branches": [
                {
                    "condition": {"function": "is_urban"},
                    "target": "urban_details"
                },
                {
                    "condition": {"function": "is_rural"},
                    "target": "rural_details"
                }
            ],
            "default_next": "REVIEW",
        },
        {
            "name": "urban_details",
            "question": "Urban area name and density?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
        {
            "name": "rural_details",
            "question": "Rural area name and population?",
            "constraints": {"type": "string"},
            "default_next": "REVIEW",
        },
    ]
    
    # Initial state: User chose "urban"
    session.responses = {
        "location": "urban",
        "urban_details": "Downtown, high density",
    }
    session.state = InterviewState.ACTIVE
    await session.save()
    
    # Verify initial state
    assert "location" in session.responses
    assert "urban_details" in session.responses
    assert "rural_details" not in session.responses
    
    # User updates location to "rural"
    session.update_response("location", "rural", "urban")
    await session.save()
    
    # Test reachability calculation
    walker = QuestionWalker()
    walker.interview_session = session
    
    # Manually mock the branch evaluator
    with patch.object(QuestionBranchEvaluator, 'matches', new_callable=AsyncMock) as mock_matches:
        # Setup mock to match the conditions based on current responses
        async def condition_evaluator(condition, session, implicit_question=None, visitor=None):
            if condition.get("function") == "is_urban":
                return session.responses.get("location") == "urban"
            elif condition.get("function") == "is_rural":
                return session.responses.get("location") == "rural"
            return False
        
        mock_matches.side_effect = condition_evaluator
        
        # Get reachable questions on new path
        reachable = await walker._get_reachable_questions(
            session, "location", "rural_details"
        )
        
        # Verify: only location and rural_details should be reachable
        assert "location" in reachable, "Branching point should be reachable"
        assert "rural_details" in reachable, "Rural details should be reachable"
        assert "urban_details" not in reachable, "Urban details should NOT be reachable"
        
        print(f"✓ Reachable questions correct: {reachable}")
        
        # Now test actual pruning
        path_changed = await walker.detect_and_prune_altered_path(
            session, "location", interview_action=None, visitor=None
        )
        
        # Should detect path change (need to record previous path first)
        # Since we haven't recorded previous path, it won't detect change
        # Let's manually set previous path info
        from jvagent.action.interview.core.utils.cache_utils import BranchFunctionCache
        cache = BranchFunctionCache(session)
        cache.record_branch_path("location", 0, "urban_details", False)
        await session.save()
        
        # Now try detect_and_prune again
        path_changed = await walker.detect_and_prune_altered_path(
            session, "location", interview_action=None, visitor=None
        )
        
        # Should detect change from urban_details to rural_details
        assert path_changed is True, "Path change should be detected"
        
        # Verify pruning
        assert "location" in session.responses, "Location should still be answered"
        assert session.responses["location"] == "rural", "Location should have new value"
        assert "urban_details" not in session.responses, "Urban details should be pruned"
        assert "rural_details" not in session.responses, "Rural details not answered yet"
        
        # Verify branch change details
        change_details = session.context.get("_branch_change_details", {})
        assert change_details is not None
        assert "urban_details" in change_details.get("pruned_questions", [])
        assert change_details.get("old_target") == "urban_details"
        assert change_details.get("new_target") == "rural_details"
        
        print(f"✓ Pruning successful: {change_details}")
        print("✓ All comprehensive tests passed!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
