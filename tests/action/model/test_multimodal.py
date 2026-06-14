"""Tests for multimodal model action functionality."""

import pytest

from jvagent.action.model import ModelActionResult
from jvagent.action.model.language.base import LanguageModelAction


class TestMultimodalContent:
    """Tests for multimodal content creation."""

    def test_create_image_content_with_url(self):
        """Test creating multimodal content with image URL."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = action.create_image_content(
            "What's in this image?", image_url="https://example.com/image.jpg"
        )

        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "What's in this image?"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "https://example.com/image.jpg"
        assert content[1]["image_url"]["detail"] == "auto"

    def test_create_image_content_with_base64(self):
        """Test creating multimodal content with base64 image."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = action.create_image_content(
            "Analyze this", image_base64="iVBORw0KGgo="
        )

        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "data:image/jpeg;base64," in content[1]["image_url"]["url"]
        assert "iVBORw0KGgo=" in content[1]["image_url"]["url"]

    def test_create_image_content_with_detail(self):
        """Test creating content with custom detail level."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = action.create_image_content(
            "Analyze in detail",
            image_url="https://example.com/image.jpg",
            image_detail="high",
        )

        assert content[1]["image_url"]["detail"] == "high"

    def test_create_multimodal_content_multiple_images(self):
        """Test creating content with multiple images."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = action.create_multimodal_content(
            "Compare these images",
            images=[
                {"url": "https://example.com/img1.jpg"},
                {"url": "https://example.com/img2.jpg", "detail": "high"},
            ],
        )

        assert len(content) == 3  # 1 text + 2 images
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[2]["type"] == "image_url"
        assert content[2]["image_url"]["detail"] == "high"

    def test_create_multimodal_content_with_base64_images(self):
        """Test creating content with base64 images."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = action.create_multimodal_content(
            "Analyze both",
            images=[
                {"base64": "abc123"},
                {"base64": "xyz789", "detail": "low"},
            ],
        )

        assert len(content) == 3
        assert "data:image/jpeg;base64,abc123" in content[1]["image_url"]["url"]
        assert "data:image/jpeg;base64,xyz789" in content[2]["image_url"]["url"]


class TestMultimodalFormatting:
    """Tests for formatting multimodal messages."""

    def test_format_messages_with_text_prompt(self):
        """Test formatting with simple text prompt."""

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

    def test_format_messages_with_multimodal_prompt(self):
        """Test formatting with multimodal prompt."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        content = [
            {"type": "text", "text": "What's this?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
        ]
        messages = action.format_messages(content)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)
        assert messages[0]["content"][0]["type"] == "text"
        assert messages[0]["content"][1]["type"] == "image_url"

    def test_format_messages_with_multimodal_history(self):
        """Test formatting with multimodal conversation history."""

        class MockModelAction(LanguageModelAction):
            async def _query(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

            async def _query_stream(self, messages, tools=None, **kwargs):
                return ModelActionResult(response="", usage={}, model="", provider="")

        action = MockModelAction()
        history = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's this?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.jpg"},
                    },
                ],
            },
            {"role": "assistant", "content": "It's a cat."},
        ]
        messages = action.format_messages("Is it cute?", history=history)

        assert len(messages) == 3
        assert isinstance(messages[0]["content"], list)  # First message has image
        assert messages[1]["content"] == "It's a cat."  # Second is text
        assert messages[2]["content"] == "Is it cute?"  # Current is text
