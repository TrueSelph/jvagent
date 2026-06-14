"""Tests for base model action classes."""

import pytest

from jvagent.action.model import ModelAction, ModelActionResult
from jvagent.action.model.language.base import LanguageModelAction


class TestModelActionResult:
    """Tests for ModelActionResult class."""

    def test_init_sync(self):
        """Test initialization with sync response."""
        result = ModelActionResult(
            response="Hello world",
            usage={"total_tokens": 10},
            model="test-model",
            provider="test",
        )

        assert result.response == "Hello world"
        assert result.metrics["total_tokens"] == 10
        assert result.model == "test-model"
        assert result.provider == "test"
        assert not result.is_streaming

    def test_init_streaming(self):
        """Test initialization with streaming response."""

        async def stream():
            yield "Hello"
            yield " world"

        result = ModelActionResult(
            stream=stream(),
            usage={},
            model="test-model",
            provider="test",
        )

        assert result.stream is not None
        assert result.is_streaming

    @pytest.mark.asyncio
    async def test_get_response_sync(self):
        """Test getting response from sync result."""
        result = ModelActionResult(
            response="Hello world",
            usage={},
            model="test",
            provider="test",
        )

        response = await result.get_response()
        assert response == "Hello world"

    @pytest.mark.asyncio
    async def test_get_response_streaming(self):
        """Test getting complete response from streaming result."""

        async def stream():
            yield "Hello"
            yield " "
            yield "world"

        result = ModelActionResult(
            stream=stream(),
            usage={},
            model="test",
            provider="test",
        )

        response = await result.get_response()
        assert response == "Hello world"

        # Should be cached
        response2 = await result.get_response()
        assert response2 == "Hello world"

    @pytest.mark.asyncio
    async def test_iter_stream_sync(self):
        """Test iterating over sync result."""
        result = ModelActionResult(
            response="Hello world",
            usage={},
            model="test",
            provider="test",
        )

        chunks = []
        async for chunk in result.iter_stream():
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    @pytest.mark.asyncio
    async def test_iter_stream_streaming(self):
        """Test iterating over streaming result."""

        async def stream():
            yield "Hello"
            yield " "
            yield "world"

        result = ModelActionResult(
            stream=stream(),
            usage={},
            model="test",
            provider="test",
        )

        chunks = []
        async for chunk in result.iter_stream():
            chunks.append(chunk)

        assert len(chunks) == 3
        assert "".join(chunks) == "Hello world"

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = ModelActionResult(
            response="Hello",
            usage={"total_tokens": 5},
            model="test-model",
            provider="test",
            finish_reason="stop",
            tool_calls=[],
        )

        data = result.to_dict()

        assert data["response"] == "Hello"
        assert data["metrics"]["total_tokens"] == 5
        assert data["model"] == "test-model"
        assert data["provider"] == "test"
        assert data["finish_reason"] == "stop"
        assert data["tool_calls"] == []
        assert not data["is_streaming"]


class TestModelAction:
    """Tests for ModelAction base class."""

    def test_format_messages_simple(self):
        """Test formatting simple messages."""

        # Use LanguageModelAction (has format_messages); ModelAction/BaseModelAction does not
        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        messages = action.format_messages("Hello")

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_format_messages_with_system(self):
        """Test formatting messages with system prompt."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        messages = action.format_messages("Hello", system="You are a helpful assistant")

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"

    def test_format_messages_with_history(self):
        """Test formatting messages with conversation history."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        messages = action.format_messages("How are you?", history=history)

        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "How are you?"

    @pytest.mark.asyncio
    async def test_track_usage(self):
        """Test usage tracking."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()

        assert action.total_requests == 0
        assert action.total_tokens == 0

        await action.track_usage({"total_tokens": 100})

        assert action.total_requests == 1
        assert action.total_tokens == 100

        await action.track_usage({"total_tokens": 50})

        assert action.total_requests == 2
        assert action.total_tokens == 150
