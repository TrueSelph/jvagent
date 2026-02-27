"""Tests for interview cache utilities (QuestionNodeCache, BranchCache)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.utils.cache_utils import (
    BranchCache,
    QuestionNodeCache,
)
from jvagent.action.interview.core.utils.constants import (
    CACHE_KEY_BRANCH_CACHE,
    CACHE_KEY_QUESTION_NODES,
)


class TestQuestionNodeCache:
    """Test QuestionNodeCache get/set/invalidate."""

    @pytest.fixture
    def mock_session(self):
        """Session with empty context."""
        session = MagicMock(spec=InterviewSession)
        session.context = {}
        return session

    def test_init_creates_cache_dict(self, mock_session):
        """Cache dict is created in session.context if missing."""
        QuestionNodeCache(mock_session)
        assert CACHE_KEY_QUESTION_NODES in mock_session.context
        assert mock_session.context[CACHE_KEY_QUESTION_NODES] == {}

    def test_init_initializes_none_context(self):
        """Session with None context gets dict with cache key."""
        session = MagicMock(spec=InterviewSession)
        session.context = None
        QuestionNodeCache(session)
        assert session.context is not None
        assert CACHE_KEY_QUESTION_NODES in session.context

    def test_get_set_invalidate(self, mock_session):
        """get/set/invalidate work correctly."""
        cache = QuestionNodeCache(mock_session)
        assert cache.get("q1") is None
        cache.set("q1", "node-id-123")
        assert cache.get("q1") == "node-id-123"
        cache.invalidate("q1")
        assert cache.get("q1") is None

    def test_invalidate_all_clears_cache(self, mock_session):
        """invalidate(None) clears entire cache."""
        cache = QuestionNodeCache(mock_session)
        cache.set("q1", "id1")
        cache.set("q2", "id2")
        cache.invalidate(None)
        assert cache.get("q1") is None
        assert cache.get("q2") is None

    @pytest.mark.asyncio
    async def test_get_cached_node_fetches_and_caches(self, mock_session):
        """get_cached_node fetches via fetch_func and caches result."""
        mock_node = MagicMock()
        mock_node.id = "node-xyz"
        fetch_func = AsyncMock(return_value=mock_node)

        cache = QuestionNodeCache(mock_session)
        result = await cache.get_cached_node("q1", fetch_func)

        assert result == mock_node
        fetch_func.assert_called_once_with("q1")
        assert cache.get("q1") == "node-xyz"

    @pytest.mark.asyncio
    async def test_get_cached_node_returns_none_when_fetch_returns_none(
        self, mock_session
    ):
        """get_cached_node returns None when fetch_func returns None."""
        fetch_func = AsyncMock(return_value=None)
        cache = QuestionNodeCache(mock_session)
        result = await cache.get_cached_node("q1", fetch_func)
        assert result is None
        assert cache.get("q1") is None

    @pytest.mark.asyncio
    async def test_get_cached_node_by_id_returns_none_when_not_cached(
        self, mock_session
    ):
        """get_cached_node_by_id returns None when question not in cache."""
        cache = QuestionNodeCache(mock_session)
        result = await cache.get_cached_node_by_id("q1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_node_by_id_returns_node_when_cached(self, mock_session):
        """get_cached_node_by_id returns node when cached and valid."""

        class FakeQuestionNode:
            pass

        fake_node = FakeQuestionNode()
        fake_node.id = "node-abc"
        cache = QuestionNodeCache(mock_session)
        cache.set("q1", "node-abc")

        with patch(
            "jvagent.action.interview.core.graph.question_node.QuestionNode",
            FakeQuestionNode,
        ):
            with patch("jvspatial.core.Node") as MockNode:
                MockNode.get = AsyncMock(return_value=fake_node)
                result = await cache.get_cached_node_by_id("q1")
        assert result is fake_node


class TestBranchCacheInvalidateFrom:
    """Test BranchCache.invalidate_from."""

    @pytest.fixture
    def mock_session(self):
        """Session with empty context."""
        session = MagicMock(spec=InterviewSession)
        session.context = {}
        return session

    def test_invalidate_from_removes_downstream(self, mock_session):
        """invalidate_from removes question and all downstream entries."""
        cache = BranchCache(mock_session)
        cache.set("q1", "t1")
        cache.set("q2", "t2")
        cache.set("q3", "t3")

        graph = [{"name": "q1"}, {"name": "q2"}, {"name": "q3"}]
        cache.invalidate_from("q2", graph)

        assert cache.get("q1") == "t1"
        assert cache.get("q2") is None
        assert cache.get("q3") is None

    def test_invalidate_from_unknown_question_no_op(self, mock_session):
        """invalidate_from with unknown question does nothing."""
        cache = BranchCache(mock_session)
        cache.set("q1", "t1")
        graph = [{"name": "q1"}]
        cache.invalidate_from("unknown", graph)
        assert cache.get("q1") == "t1"

    def test_clear_pruned_responses(self, mock_session):
        """clear_pruned_responses empties pruned responses."""
        cache = BranchCache(mock_session)
        cache.record_pruned_response("q1", "old", "reason")
        assert cache.get_pruned_responses()
        cache.clear_pruned_responses()
        assert cache.get_pruned_responses() == {}
