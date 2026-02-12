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

class TestPostUpdatePruning:
    """Tests for PostUpdateWalker-based pruning on path change."""

    @pytest.mark.asyncio
    async def test_no_pruning_when_all_reachable(
        self, test_session_with_dynamic_branches
    ):
        """No responses should be pruned when all answered questions are reachable."""
        from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker

        session = test_session_with_dynamic_branches
        session.responses = {
            "report_description": "normal incident",
            "contact_info": "test@example.com",
        }
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"report_description", "contact_info"}

        walker._prune_session()

        assert "report_description" in session.responses
        assert "contact_info" in session.responses

    @pytest.mark.asyncio
    async def test_pruning_removes_old_branch_response(
        self, test_session_with_dynamic_branches
    ):
        """When path switches from is_sensitive to contact_info, is_sensitive is pruned."""
        from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker

        session = test_session_with_dynamic_branches
        session.responses = {
            "report_description": "urgent incident",
            "is_sensitive": "yes",
        }
        await session.save()

        # New reachable path: report_description -> contact_info (default_next)
        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"report_description", "contact_info"}

        walker._prune_session()

        assert "is_sensitive" not in session.responses, "is_sensitive should be pruned"
        assert "report_description" in session.responses, "report_description should remain"

    @pytest.mark.asyncio
    async def test_pruned_response_audit_trail(
        self, test_session_with_dynamic_branches
    ):
        """Pruned responses are recorded in the BranchCache audit trail."""
        from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker

        session = test_session_with_dynamic_branches
        session.responses = {
            "report_description": "test",
            "is_sensitive": "yes",
        }
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"report_description", "contact_info"}

        walker._prune_session()

        pruned = BranchCache(session).get_pruned_responses()
        assert "is_sensitive" in pruned
        assert pruned["is_sensitive"]["value"] == "yes"
        assert pruned["is_sensitive"]["reason"] == "branch_path_change"

    @pytest.mark.asyncio
    async def test_is_state_target_helper(self):
        """InterviewWalker._is_state_target correctly identifies state targets."""
        from jvagent.action.interview.core.graph.interview_walker import InterviewWalker

        walker = InterviewWalker()
        assert walker._is_state_target("REVIEW") is True
        assert walker._is_state_target("COMPLETED") is True
        assert walker._is_state_target("CANCELLED") is True
        assert walker._is_state_target("ACTIVE") is False
        assert walker._is_state_target("some_question") is False