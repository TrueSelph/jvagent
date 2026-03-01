"""Tests for usage aggregation (Interaction.compute_usage, User.add_usage_from_interaction)."""

import uuid

import pytest

from jvagent.action.model.cost_estimator import estimate_cost
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.user import User


class TestCostEstimator:
    """Tests for cost_estimator utility."""

    def test_estimate_cost_model_call_known_model(self):
        """Estimate cost for known OpenAI model."""
        cost = estimate_cost(
            model="gpt-4o-mini",
            provider="openai",
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
            event_type="model_call",
        )
        assert cost > 0
        assert cost < 0.01  # 1.5k tokens at gpt-4o-mini rates

    def test_estimate_cost_embedding_call(self):
        """Estimate cost for embedding model."""
        cost = estimate_cost(
            model="text-embedding-3-small",
            provider="openai",
            usage={"total_tokens": 1000},
            event_type="embedding_call",
        )
        assert cost > 0
        assert cost < 0.001

    def test_estimate_cost_empty_usage_returns_zero(self):
        """Empty usage returns 0."""
        assert estimate_cost("gpt-4o", "openai", {}, "model_call") == 0.0
        assert estimate_cost("gpt-4o", "openai", None, "model_call") == 0.0


class TestInteractionComputeUsage:
    """Tests for Interaction.compute_usage()."""

    def test_compute_usage_empty_metrics(self):
        """Empty observability_metrics yields zero usage."""
        obj = _make_interaction_like()
        obj.observability_metrics = []
        result = Interaction.compute_usage(obj)
        assert result["total_tokens"] == 0
        assert result["model_call_count"] == 0
        assert result["estimated_cost_usd"] == 0.0
        assert "last_updated" in result

    def test_compute_usage_single_model_call(self):
        """Single model_call aggregates correctly."""
        obj = _make_interaction_like()
        obj.observability_metrics = [
            {
                "event_type": "model_call",
                "data": {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                    "duration": 0.25,
                    "model": "gpt-4o-mini",
                    "provider": "openai",
                },
            },
        ]
        result = Interaction.compute_usage(obj)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["model_call_count"] == 1
        assert result["total_duration_seconds"] == 0.25
        assert result["estimated_cost_usd"] > 0
        assert obj.usage == result

    def test_compute_usage_mixed_events(self):
        """Model and embedding calls both counted."""
        obj = _make_interaction_like()
        obj.observability_metrics = [
            {
                "event_type": "model_call",
                "data": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "duration": 0.1,
                    "model": "gpt-4o-mini",
                    "provider": "openai",
                },
            },
            {
                "event_type": "embedding_call",
                "data": {
                    "usage": {"total_tokens": 20},
                    "duration": 0.05,
                    "model": "text-embedding-3-small",
                    "provider": "openai",
                },
            },
        ]
        result = Interaction.compute_usage(obj)
        assert result["model_call_count"] == 1
        assert result["total_tokens"] == 35  # 15 + 20
        assert result["total_duration_seconds"] == 0.15

    def test_get_state_includes_usage(self):
        """get_state() includes usage in the returned dict."""
        obj = _make_interaction_like()
        obj.id = None
        obj.conversation_id = ""
        obj.user_id = ""
        obj.session_id = ""
        obj.utterance = ""
        obj.channel = "default"
        obj.response = None
        obj.actions = []
        obj.directives = []
        obj.parameters = []
        obj.events = []
        obj.interpretation = None
        obj.anchors = []
        obj.started_at = None
        obj.completed_at = None
        obj.closed = False
        obj.streamed = False
        obj.observability_metrics = [
            {
                "event_type": "model_call",
                "data": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "duration": 0.1,
                    "model": "gpt-4o-mini",
                    "provider": "openai",
                },
            },
        ]
        Interaction.compute_usage(obj)
        obj.get_state = lambda: Interaction.get_state(obj)
        state = obj.get_state()
        assert "usage" in state
        assert state["usage"]["total_tokens"] == 15
        assert state["usage"]["model_call_count"] == 1


def _make_interaction_like():
    """Create a minimal object that Interaction.compute_usage can operate on."""
    obj = type("Obj", (), {})()
    obj.observability_metrics = []
    obj.usage = {}
    return obj


class TestUserAddUsageFromInteraction:
    """Tests for User.add_usage_from_interaction()."""

    @pytest.mark.asyncio
    async def test_add_usage_from_interaction_initializes_stats(self, test_db):
        """add_usage_from_interaction initializes and increments usage."""
        user = await User.create(user_id=f"test-user-{id(object())}")
        try:
            assert not user.usage or user.usage.get("total_tokens", 0) == 0

            usage = {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "model_call_count": 1,
                "estimated_cost_usd": 0.001,
                "total_duration_seconds": 0.5,
            }
            await user.add_usage_from_interaction(usage)

            stats = user.get_usage_statistics()
            assert stats["total_tokens"] == 150
            assert stats["prompt_tokens"] == 100
            assert stats["completion_tokens"] == 50
            assert stats["model_call_count"] == 1
            assert stats["interaction_count"] == 1
            assert stats["estimated_cost_usd"] == 0.001
            assert stats["last_updated"] is not None
        finally:
            await user.delete(cascade=True)

    @pytest.mark.asyncio
    async def test_add_usage_from_interaction_accumulates(self, test_db):
        """add_usage_from_interaction accumulates across multiple calls."""
        user = await User.create(user_id=f"test-user-{id(object())}")
        try:
            await user.add_usage_from_interaction(
                {
                    "total_tokens": 100,
                    "prompt_tokens": 60,
                    "completion_tokens": 40,
                    "model_call_count": 1,
                    "estimated_cost_usd": 0.0005,
                    "total_duration_seconds": 0.2,
                }
            )
            await user.add_usage_from_interaction(
                {
                    "total_tokens": 50,
                    "prompt_tokens": 30,
                    "completion_tokens": 20,
                    "model_call_count": 1,
                    "estimated_cost_usd": 0.0002,
                    "total_duration_seconds": 0.1,
                }
            )

            stats = user.get_usage_statistics()
            assert stats["total_tokens"] == 150
            assert stats["interaction_count"] == 2
        finally:
            await user.delete(cascade=True)

    @pytest.mark.asyncio
    async def test_add_usage_from_interaction_empty_noop(self, test_db):
        """Empty usage does not modify user."""
        user = await User.create(user_id=f"test-user-{id(object())}")
        try:
            await user.add_usage_from_interaction({})
            stats = user.get_usage_statistics()
            assert stats["total_tokens"] == 0
            assert stats["interaction_count"] == 0
        finally:
            await user.delete(cascade=True)


class TestUserGetUsageStatistics:
    """Tests for User.get_usage_statistics()."""

    def test_get_usage_statistics_empty_returns_defaults(self):
        """Empty usage returns sensible defaults."""
        user = type("User", (), {"usage": {}})()
        stats = User.get_usage_statistics(user)
        assert stats["total_tokens"] == 0
        assert stats["model_call_count"] == 0
        assert stats["interaction_count"] == 0
        assert stats["last_updated"] is None


class TestConversationGetStatisticsFallback:
    """Tests for Conversation.get_statistics() fallback when interactions lack usage."""

    @pytest.mark.asyncio
    async def test_get_statistics_fallback_aggregates_from_observability(self, test_db):
        """When interactions lack usage, get_statistics aggregates from observability_metrics."""
        conv = await Conversation.create(
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            user_id="user1",
            channel="default",
        )
        try:
            interaction = await conv.create_interaction("Hello")
            interaction.observability_metrics = [
                {
                    "event_type": "model_call",
                    "data": {
                        "usage": {
                            "prompt_tokens": 50,
                            "completion_tokens": 25,
                            "total_tokens": 75,
                        },
                        "duration": 0.2,
                        "model": "gpt-4o-mini",
                        "provider": "openai",
                    },
                },
            ]
            await interaction.save()

            stats = await conv.get_statistics()
            assert stats["total_tokens"] == 75
            assert stats["total_duration"] == 0.2
        finally:
            await conv.delete(cascade=True)
