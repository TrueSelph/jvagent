"""Tests for branch caching and path recording."""

import pytest
from unittest.mock import AsyncMock

from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.utils.cache_utils import BranchCache


@pytest.fixture
async def test_session_with_dynamic_branches(test_db):
    """Create a test session with function-based conditional branches."""
    question_index = [
        {
            "name": "report_description",
            "question": "Describe the incident...",
            "constraints": {"description": "Incident description", "type": "string"},
            "required": True,
            "branches": [
                {
                    "condition": {"function": "check_contains_sensitive_info"},
                    "target": "is_sensitive"
                },
                {
                    "condition": {"function": "calculate_urgency_score", "op": ">=", "value": 8},
                    "target": "urgent_escalation"
                }
            ],
            "default_next": "contact_info"
        },
        {
            "name": "is_sensitive",
            "question": "Does this involve sensitive information?",
            "constraints": {"description": "Sensitive flag", "type": "string"},
            "required": False,
            "default_next": "contact_info"
        },
        {
            "name": "urgent_escalation",
            "question": "This is urgent. Let me connect you to someone now.",
            "constraints": {"description": "Urgent routing", "type": "string"},
            "required": False,
            "default_next": "contact_info"
        },
        {
            "name": "contact_info",
            "question": "What's your contact information?",
            "constraints": {"description": "Contact info", "type": "string"},
            "required": True
        }
    ]
    
    session = await InterviewSession.create(
        agent_id="test_agent",
        conversation_id="test_conv",
        interview_type="TestInterviewAction",
        question_index=question_index,
        state=InterviewState.ACTIVE,
    )
    # Manually set question_graph from question_index if not set
    if not session.question_graph:
        session.question_graph = question_index
    return session


class TestBranchCaching:
    """Test branch cache (question -> target) and path recording."""
    
    @pytest.mark.asyncio
    async def test_cache_stores_and_retrieves_target(self, test_session_with_dynamic_branches):
        """Test that resolved branch target is cached and reused."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        
        assert branch_cache.get("report_description") is None
        branch_cache.set("report_description", "is_sensitive")
        assert branch_cache.get("report_description") == "is_sensitive"
    
    @pytest.mark.asyncio
    async def test_cache_invalidation_clears_entry(self, test_session_with_dynamic_branches):
        """Test that invalidate(question_name) clears that question's cached target."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.set("report_description", "is_sensitive")
        assert branch_cache.get("report_description") == "is_sensitive"
        branch_cache.invalidate("report_description")
        assert branch_cache.get("report_description") is None
    
    @pytest.mark.asyncio
    async def test_invalidate_targets_single_entry(self, test_session_with_dynamic_branches):
        """Test targeted invalidation leaves other entries intact."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.set("report_description", "is_sensitive")
        branch_cache.set("contact_info", "urgent_escalation")
        branch_cache.invalidate("report_description")
        assert branch_cache.get("report_description") is None
        assert branch_cache.get("contact_info") == "urgent_escalation"
    
    @pytest.mark.asyncio
    async def test_branch_path_recording(self, test_session_with_dynamic_branches):
        """Test that branch paths are recorded for change detection."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.record_branch_path(
            "report_description",
            condition_index=0,
            target="is_sensitive",
            is_default=False
        )
        path = branch_cache.get_previous_path("report_description")
        assert path is not None
        assert path["target"] == "is_sensitive"
    
    @pytest.mark.asyncio
    async def test_pruned_response_tracking(self, test_session_with_dynamic_branches):
        """Test that pruned responses are tracked for audit trail."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.record_pruned_response(
            "is_sensitive",
            "yes",
            "branch_path_change: is_sensitive -> urgent_escalation"
        )
        pruned = branch_cache.get_pruned_responses()
        assert "is_sensitive" in pruned
        assert pruned["is_sensitive"]["value"] == "yes"
        assert "branch_path_change" in pruned["is_sensitive"]["reason"]
    
    @pytest.mark.asyncio
    async def test_invalidate_all_clears_cache(self, test_session_with_dynamic_branches):
        """Test invalidate_all clears entire branch cache."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.set("report_description", "is_sensitive")
        branch_cache.set("contact_info", "urgent_escalation")
        branch_cache.invalidate_all()
        assert branch_cache.get("report_description") is None
        assert branch_cache.get("contact_info") is None


class TestBranchDependencyTracking:
    """Test automatic response dependency tracking in branch functions."""
    
    @pytest.mark.asyncio
    async def test_dependency_tracking_context_manager(self):
        """Test that response access is tracked via context manager."""
        from jvagent.action.interview.core.foundation.decorators import (
            track_response_access,
            get_tracked_responses
        )
        
        with track_response_access() as tracker:
            # Access tracking should be active
            assert get_tracked_responses() is not None
            # Simulate tracking some keys
            from jvagent.action.interview.core.foundation.decorators import record_response_access
            record_response_access("field1")
            record_response_access("field2")
            
            # Should have tracked accesses
            tracked = tracker.get()
            assert "field1" in tracked
            assert "field2" in tracked
        
        # After context, tracking should be cleared
        assert get_tracked_responses() is None
    
    @pytest.mark.asyncio
    async def test_instrumented_responses_dict_access(self):
        """Test that _InstrumentedResponses tracks all access methods."""
        from jvagent.action.interview.core.foundation.decorators import (
            _InstrumentedResponses,
            track_response_access
        )
        
        original = {"field1": "value1", "field2": "value2"}
        instrumented = _InstrumentedResponses(original)
        
        with track_response_access() as tracker:
            # Test .get() access
            val = instrumented.get("field1")
            assert val == "value1"
            
            # Test __getitem__ access
            val = instrumented["field2"]
            assert val == "value2"
            
            # Test __contains__ access
            assert "field1" in instrumented
            
            tracked = tracker.get()
            assert "field1" in tracked
            assert "field2" in tracked


class TestResponsePruning:
    """Test intelligent response pruning when branch paths change."""
    
    @pytest.mark.asyncio
    async def test_detects_path_change(self, test_session_with_dynamic_branches):
        """Test detection of branch path changes."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.record_branch_path(
            "report_description",
            condition_index=0,
            target="is_sensitive",
            is_default=False
        )
        new_path = "urgent_escalation"
        old_path = branch_cache.get_previous_path("report_description")
        assert old_path["target"] != new_path
    
    @pytest.mark.asyncio
    async def test_pruned_response_contains_all_data(self, test_session_with_dynamic_branches):
        """Test that pruned responses maintain complete audit trail."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        
        # Record multiple pruned responses
        branch_cache.record_pruned_response("is_sensitive", "yes", "path_change")
        branch_cache.record_pruned_response("urgent_escalation", None, "path_change")
        
        pruned = branch_cache.get_pruned_responses()
        assert len(pruned) == 2
        
        # Each should have required fields
        for question_name, data in pruned.items():
            assert "value" in data
            assert "reason" in data
            assert "pruned_at" in data
            assert "dependency_snapshot" in data


class TestBranchCachingIntegration:
    """Integration tests for branch caching with question walker."""
    
    @pytest.mark.asyncio
    async def test_cache_get_set_consistency(self, test_session_with_dynamic_branches):
        """Test that get returns the same target that was set."""
        session = test_session_with_dynamic_branches
        branch_cache = BranchCache(session)
        branch_cache.set("report_description", "is_sensitive")
        assert branch_cache.get("report_description") == "is_sensitive"
        branch_cache.set("report_description", "contact_info")
        assert branch_cache.get("report_description") == "contact_info"

class TestInterviewReset:
    """Tests for interview reset on path change."""
    
    @pytest.mark.asyncio
    async def test_detect_and_prune_returns_false_when_no_path_change(
        self, test_session_with_dynamic_branches
    ):
        """Test that detect_and_prune_altered_path returns False when path hasn't changed."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker
        
        session = test_session_with_dynamic_branches
        session.responses = {"report_description": "normal incident"}
        session.active_question_key = "report_description"
        
        walker = QuestionWalker()
        result = await walker.detect_and_prune_altered_path(
            session, "report_description"
        )
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_detect_and_prune_returns_true_when_path_changes(
        self, test_session_with_dynamic_branches
    ):
        """Test that detect_and_prune_altered_path returns True when path changes."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker
        from unittest.mock import AsyncMock, patch
        
        session = test_session_with_dynamic_branches
        session.responses = {"report_description": "urgent incident with sensitive info"}
        session.active_question_key = "report_description"
        await session.save()
        
        branch_cache = BranchCache(session)
        branch_cache.record_branch_path("report_description", 0, "is_sensitive", False)
        await session.save()
        
        walker = QuestionWalker()
        
        # Mock branch evaluator to simulate path change (all conditions false → default_next)
        with patch.object(
            QuestionBranchEvaluator,
            "matches",
            new_callable=AsyncMock,
            return_value=False  # All conditions false, will use default_next
        ):
            result = await walker.detect_and_prune_altered_path(
                session, "report_description"
            )
        
        # Path changed (was "is_sensitive", now "contact_info" via default_next)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_reset_sets_active_to_branching_question(
        self, test_session_with_dynamic_branches
    ):
        """Test that reset sets active_question_key to the branching question for stepwise traversal."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker

        session = test_session_with_dynamic_branches
        session.active_question_key = "is_sensitive"

        walker = QuestionWalker()
        await walker._reset_to_branching_point(
            session,
            "report_description",
            "contact_info"
        )

        # After reset, active_question_key should point to the branching question
        # so walker starts there and takes one step to new_target (stepwise traversal)
        assert session.active_question_key == "report_description"
    
    @pytest.mark.asyncio
    async def test_reset_records_audit_trail(
        self, test_session_with_dynamic_branches
    ):
        """Test that reset records audit trail in session context."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker
        
        session = test_session_with_dynamic_branches
        session.active_question_key = "is_sensitive"
        
        walker = QuestionWalker()
        await walker._reset_to_branching_point(
            session,
            "report_description",
            "contact_info"
        )
        
        # Check audit trail
        assert "_interview_resets" in session.context
        resets = session.context["_interview_resets"]
        assert len(resets) > 0
        
        last_reset = resets[-1]
        assert last_reset["branching_question"] == "report_description"
        assert last_reset["new_target"] == "contact_info"
        assert "timestamp" in last_reset
    
    @pytest.mark.asyncio
    async def test_prune_responses_and_reset_together(
        self, test_session_with_dynamic_branches
    ):
        """Test that pruning and reset happen together on path change."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker
        from unittest.mock import AsyncMock, patch
        
        session = test_session_with_dynamic_branches
        session.responses = {
            "report_description": "urgent incident",
            "is_sensitive": "yes",  # This will be pruned if path changes
        }
        session.active_question_key = "is_sensitive"
        await session.save()
        
        branch_cache = BranchCache(session)
        branch_cache.record_branch_path("report_description", 0, "is_sensitive", False)
        await session.save()
        
        walker = QuestionWalker()
        
        # Mock branch evaluator to simulate all conditions false (path changes to default_next)
        with patch.object(
            QuestionBranchEvaluator,
            "matches",
            new_callable=AsyncMock,
            return_value=False
        ):
            result = await walker.detect_and_prune_altered_path(
                session, "report_description"
            )
        
        # Path changed and interview reset
        assert result is True
        # After reset, active_question_key points to the branching question (stepwise traversal)
        assert session.active_question_key == "report_description"
        
        # When path changes from is_sensitive to contact_info (default_next),
        # is_sensitive is no longer on the path and should be pruned
        assert "is_sensitive" not in session.responses, "is_sensitive should be pruned from old path"
        assert "report_description" in session.responses, "report_description should remain"
        
        # Check that branch change details were recorded
        change_details = session.context.get("_branch_change_details", {})
        assert change_details.get("old_target") == "is_sensitive"
        assert change_details.get("new_target") == "contact_info"
    
    @pytest.mark.asyncio
    async def test_multiple_resets_recorded(
        self, test_session_with_dynamic_branches
    ):
        """Test that multiple resets are recorded in audit trail."""
        from jvagent.action.interview.core.graph.question_walker import QuestionWalker
        
        session = test_session_with_dynamic_branches
        walker = QuestionWalker()
        
        # Perform multiple resets
        await walker._reset_to_branching_point(
            session, "report_description", "is_sensitive"
        )
        
        await walker._reset_to_branching_point(
            session, "report_description", "urgent_escalation"
        )
        
        # Check that both resets were recorded
        resets = session.context.get("_interview_resets", [])
        assert len(resets) >= 2
        assert resets[0]["new_target"] == "is_sensitive"
        assert resets[1]["new_target"] == "urgent_escalation"