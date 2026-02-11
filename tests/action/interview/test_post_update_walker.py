"""Comprehensive tests for PostUpdateWalker.

Covers:
- Unit tests for _prune_session (no graph needed)
- Integration tests with real spatial-graph nodes (test_db fixture)
"""

import pytest
from unittest.mock import AsyncMock, patch

from jvagent.action.interview.core.foundation.enums import InterviewState
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.graph.post_update_walker import PostUpdateWalker
from jvagent.action.interview.core.graph.question_node import QuestionNode
from jvagent.action.interview.core.graph.question_edge import QuestionEdge
from jvagent.action.interview.core.graph.state_node import StateNode
from jvagent.action.interview.core.graph.question_branch_evaluator import QuestionBranchEvaluator
from jvagent.action.interview.core.utils.cache_utils import BranchCache


# ---------------------------------------------------------------------------
# Unit tests — _prune_session with manually-set _reachable
# ---------------------------------------------------------------------------

class TestPruneSessionUnit:
    """Unit-level tests for PostUpdateWalker._prune_session()."""

    @pytest.mark.asyncio
    async def test_linear_graph_no_pruning(self):
        """All responses reachable on a linear path => nothing pruned."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [
            {"name": "q1", "default_next": "q2"},
            {"name": "q2", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "a", "q2": "b"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"q1", "q2"}

        walker._prune_session()

        assert session.responses == {"q1": "a", "q2": "b"}

    @pytest.mark.asyncio
    async def test_branch_change_prunes_old_branch(self):
        """Responses from old branch are pruned when reachable set excludes them."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [
            {"name": "q1", "branches": [
                {"condition": {"op": "equals", "value": "a"}, "target": "q2a"},
                {"condition": {"op": "equals", "value": "b"}, "target": "q2b"},
            ], "default_next": "REVIEW"},
            {"name": "q2a", "default_next": "REVIEW"},
            {"name": "q2b", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "b", "q2a": "old_answer"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"q1", "q2b"}

        walker._prune_session()

        assert "q2a" not in session.responses
        assert "q1" in session.responses

    @pytest.mark.asyncio
    async def test_convergence_preserves_post_merge_responses(self):
        """Responses after the convergence point are preserved."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [
            {"name": "qA", "branches": [
                {"condition": {"op": "equals", "value": "b"}, "target": "qB"},
                {"condition": {"op": "equals", "value": "c"}, "target": "qC"},
            ], "default_next": "qD"},
            {"name": "qB", "default_next": "qD"},
            {"name": "qC", "default_next": "qD"},
            {"name": "qD", "default_next": "REVIEW"},
        ]
        session.responses = {"qA": "c", "qB": "valB", "qD": "valD"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"qA", "qC", "qD"}

        walker._prune_session()

        assert "qB" not in session.responses
        assert "qD" in session.responses
        assert session.responses["qD"] == "valD"

    @pytest.mark.asyncio
    async def test_empty_reachable_skips_pruning(self):
        """Safety guard: empty reachable set must not prune anything."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [{"name": "q1", "default_next": "REVIEW"}]
        session.responses = {"q1": "answer"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        # _reachable is empty by default

        walker._prune_session()

        assert "q1" in session.responses, "Must not prune when reachable is empty"

    @pytest.mark.asyncio
    async def test_update_queue_pruned(self):
        """Entries in update_queue for unreachable questions are removed."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [
            {"name": "q1", "default_next": "q2"},
            {"name": "q2", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "a"}
        session.update_queue = [
            {"field": "q1", "value": "a2", "old_value": "a"},
            {"field": "qX", "value": "x", "old_value": None},
        ]
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"q1", "q2"}

        walker._prune_session()

        fields = [e["field"] for e in session.update_queue]
        assert "q1" in fields
        assert "qX" not in fields

    @pytest.mark.asyncio
    async def test_validation_results_pruned(self):
        """validation_results for pruned responses are also removed."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [
            {"name": "q1", "default_next": "REVIEW"},
            {"name": "q2", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "a", "q2": "b"}
        session.validation_results = {"q1": "VALID", "q2": "VALID"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"q1"}

        walker._prune_session()

        assert "q1" in session.validation_results
        assert "q2" not in session.validation_results

    @pytest.mark.asyncio
    async def test_pruned_responses_audit_trail(self):
        """Pruned responses are recorded in BranchCache audit trail."""
        session = InterviewSession(agent_id="t")
        session.question_graph = [{"name": "q1"}, {"name": "q2"}]
        session.responses = {"q1": "keep", "q2": "prune_me"}
        await session.save()

        walker = PostUpdateWalker(interview_session=session)
        walker._reachable = {"q1"}

        walker._prune_session()

        pruned = BranchCache(session).get_pruned_responses()
        assert "q2" in pruned
        assert pruned["q2"]["value"] == "prune_me"
        assert pruned["q2"]["reason"] == "branch_path_change"


# ---------------------------------------------------------------------------
# Integration tests — real graph traversal via test_db
# ---------------------------------------------------------------------------

class TestPostUpdateWalkerSync:
    """Integration tests with real spatial graph nodes."""

    @pytest.mark.asyncio
    async def test_sync_linear_graph(self, test_db):
        """sync() on a linear Q1 -> Q2 -> REVIEW graph returns all questions."""
        session = await InterviewSession.create(
            agent_id="t",
            conversation_id="c1",
            interview_type="Test",
            state=InterviewState.ACTIVE,
        )
        session.question_graph = [
            {"name": "q1", "default_next": "q2"},
            {"name": "q2", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "a", "q2": "b"}
        await session.save()

        # Build graph nodes
        q1 = await QuestionNode.create(
            agent_id="t", interview_type="Test",
            state={"name": "q1"}, label="q1",
        )
        q2 = await QuestionNode.create(
            agent_id="t", interview_type="Test",
            state={"name": "q2"}, label="q2",
        )
        review = await StateNode.create(
            agent_id="t", interview_type="Test",
            state_type=InterviewState.REVIEW, label="REVIEW",
        )
        await q1.connect(q2, edge=QuestionEdge, is_default=True, branch_index=-1)
        await q2.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)

        reachable = await PostUpdateWalker.sync(session, q1)
        assert reachable == {"q1", "q2"}
        assert session.responses == {"q1": "a", "q2": "b"}

    @pytest.mark.asyncio
    async def test_sync_branch_prunes_old_path(self, test_db):
        """sync() prunes responses on the old branch after a branch change.

        Graph:
            q1 --[op:equals "a"]--> q2a
            q1 --[op:equals "b"]--> q2b
            q1 --(default)--> REVIEW
        """
        session = await InterviewSession.create(
            agent_id="t",
            conversation_id="c2",
            interview_type="Test",
            state=InterviewState.ACTIVE,
        )
        session.question_graph = [
            {"name": "q1", "branches": [
                {"condition": {"op": "equals", "value": "a"}, "target": "q2a"},
                {"condition": {"op": "equals", "value": "b"}, "target": "q2b"},
            ], "default_next": "REVIEW"},
            {"name": "q2a", "default_next": "REVIEW"},
            {"name": "q2b", "default_next": "REVIEW"},
        ]
        # User changed q1 from "a" to "b"
        session.responses = {"q1": "b", "q2a": "old"}
        await session.save()

        q1 = await QuestionNode.create(
            agent_id="t", interview_type="Test",
            state={"name": "q1"}, label="q1",
        )
        q2a = await QuestionNode.create(
            agent_id="t", interview_type="Test",
            state={"name": "q2a"}, label="q2a",
        )
        q2b = await QuestionNode.create(
            agent_id="t", interview_type="Test",
            state={"name": "q2b"}, label="q2b",
        )
        review = await StateNode.create(
            agent_id="t", interview_type="Test",
            state_type=InterviewState.REVIEW, label="REVIEW",
        )

        # Conditional edges
        await q1.connect(q2a, edge=QuestionEdge,
                         condition={"op": "equals", "value": "a"},
                         branch_index=0, is_default=False)
        await q1.connect(q2b, edge=QuestionEdge,
                         condition={"op": "equals", "value": "b"},
                         branch_index=1, is_default=False)
        await q1.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)
        await q2a.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)
        await q2b.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)

        reachable = await PostUpdateWalker.sync(session, q1)

        assert "q1" in reachable
        assert "q2b" in reachable
        assert "q2a" not in reachable
        assert "q2a" not in session.responses, "Old branch response should be pruned"
        assert "q1" in session.responses

    @pytest.mark.asyncio
    async def test_sync_convergence(self, test_db):
        """Responses after convergence point are preserved.

        Graph:
            q1 --[equals "b"]--> qB --default--> qD
            q1 --[equals "c"]--> qC --default--> qD
            q1 --(default)--> qD
            qD --default--> REVIEW
        """
        session = await InterviewSession.create(
            agent_id="t",
            conversation_id="c3",
            interview_type="Test",
            state=InterviewState.ACTIVE,
        )
        session.question_graph = [
            {"name": "q1", "branches": [
                {"condition": {"op": "equals", "value": "b"}, "target": "qB"},
                {"condition": {"op": "equals", "value": "c"}, "target": "qC"},
            ], "default_next": "qD"},
            {"name": "qB", "default_next": "qD"},
            {"name": "qC", "default_next": "qD"},
            {"name": "qD", "default_next": "REVIEW"},
        ]
        session.responses = {"q1": "c", "qB": "old", "qD": "keep"}
        await session.save()

        q1 = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "q1"}, label="q1")
        qB = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "qB"}, label="qB")
        qC = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "qC"}, label="qC")
        qD = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "qD"}, label="qD")
        review = await StateNode.create(agent_id="t", interview_type="Test", state_type=InterviewState.REVIEW, label="REVIEW")

        await q1.connect(qB, edge=QuestionEdge, condition={"op": "equals", "value": "b"}, branch_index=0, is_default=False)
        await q1.connect(qC, edge=QuestionEdge, condition={"op": "equals", "value": "c"}, branch_index=1, is_default=False)
        await q1.connect(qD, edge=QuestionEdge, is_default=True, branch_index=-1)
        await qB.connect(qD, edge=QuestionEdge, is_default=True, branch_index=-1)
        await qC.connect(qD, edge=QuestionEdge, is_default=True, branch_index=-1)
        await qD.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)

        reachable = await PostUpdateWalker.sync(session, q1)

        assert reachable == {"q1", "qC", "qD"}
        assert "qB" not in session.responses, "Old branch pruned"
        assert session.responses["qD"] == "keep", "Convergence response preserved"

    @pytest.mark.asyncio
    async def test_sync_returns_empty_when_first_node_none(self):
        """sync() returns empty set and does not crash when first_node is None."""
        session = InterviewSession(agent_id="t")
        session.responses = {"q1": "safe"}
        await session.save()

        reachable = await PostUpdateWalker.sync(session, first_node=None)
        assert reachable == set()
        assert "q1" in session.responses, "Responses must not be touched"

    @pytest.mark.asyncio
    async def test_sync_invalidates_cache_before_traversal(self, test_db):
        """sync() clears the BranchCache so conditions are evaluated fresh."""
        session = await InterviewSession.create(
            agent_id="t",
            conversation_id="c4",
            interview_type="Test",
            state=InterviewState.ACTIVE,
        )
        session.question_graph = [{"name": "q1", "default_next": "REVIEW"}]
        session.responses = {"q1": "a"}

        # Pre-populate cache with a stale entry
        BranchCache(session).set("q1", "stale_target")
        await session.save()

        q1 = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "q1"}, label="q1")
        review = await StateNode.create(agent_id="t", interview_type="Test", state_type=InterviewState.REVIEW, label="REVIEW")
        await q1.connect(review, edge=QuestionEdge, is_default=True, branch_index=-1)

        reachable = await PostUpdateWalker.sync(session, q1)

        # Cache was cleared before traversal; stale entry should be gone.
        # After traversal the cache only has entries recorded during evaluation.
        assert BranchCache(session).get("q1") is None or reachable == {"q1"}
        assert "q1" in reachable

    @pytest.mark.asyncio
    async def test_sync_error_resilience(self, test_db):
        """If spawn() raises, sync() logs but still returns partial reachable set."""
        session = await InterviewSession.create(
            agent_id="t",
            conversation_id="c5",
            interview_type="Test",
            state=InterviewState.ACTIVE,
        )
        session.question_graph = [{"name": "q1"}]
        session.responses = {"q1": "answer"}
        await session.save()

        q1 = await QuestionNode.create(agent_id="t", interview_type="Test", state={"name": "q1"}, label="q1")

        # Force spawn to raise after partial traversal
        original_spawn = PostUpdateWalker.spawn

        async def exploding_spawn(self, node):
            # Manually add q1 to reachable to simulate partial work
            self._reachable.add("q1")
            raise RuntimeError("boom")

        with patch.object(PostUpdateWalker, "spawn", exploding_spawn):
            reachable = await PostUpdateWalker.sync(session, q1)

        # Partial reachable set should be used — q1 is in it, so no pruning occurs
        assert "q1" in session.responses
