"""Unit tests for generate_image_interpretation in multimodal.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.vision.multimodal import generate_image_interpretation


def _make_model_action(response_text: str = "a detailed description") -> MagicMock:
    action = MagicMock()
    action.create_multimodal_content = MagicMock(return_value=["<multimodal-prompt>"])
    action.generate = AsyncMock(return_value=response_text)
    return action


@pytest.mark.asyncio
async def test_returns_empty_string_when_no_urls():
    action = _make_model_action()
    result = await generate_image_interpretation([], action)
    assert result == ""
    action.generate.assert_not_called()


@pytest.mark.asyncio
async def test_basic_call_no_overrides():
    """No model/temperature/max_tokens → generate called without those kwargs."""
    action = _make_model_action("nice image")
    result = await generate_image_interpretation(
        [{"url": "https://example.com/img.jpg"}], action
    )

    assert result == "nice image"
    _, kwargs = action.generate.call_args
    assert "model" not in kwargs
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_model_kwarg_forwarded():
    action = _make_model_action()
    await generate_image_interpretation(
        [{"url": "https://example.com/img.jpg"}],
        action,
        model="gpt-4o",
    )
    _, kwargs = action.generate.call_args
    assert kwargs["model"] == "gpt-4o"
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_temperature_kwarg_forwarded():
    action = _make_model_action()
    await generate_image_interpretation(
        [{"url": "https://example.com/img.jpg"}],
        action,
        temperature=0.1,
    )
    _, kwargs = action.generate.call_args
    assert kwargs["temperature"] == 0.1
    assert "model" not in kwargs


@pytest.mark.asyncio
async def test_max_tokens_kwarg_forwarded():
    action = _make_model_action()
    await generate_image_interpretation(
        [{"url": "https://example.com/img.jpg"}],
        action,
        max_tokens=512,
    )
    _, kwargs = action.generate.call_args
    assert kwargs["max_tokens"] == 512


@pytest.mark.asyncio
async def test_all_overrides_forwarded():
    action = _make_model_action()
    await generate_image_interpretation(
        ["https://example.com/img.jpg"],
        action,
        model="claude-opus-4-7",
        temperature=0.0,
        max_tokens=1024,
    )
    _, kwargs = action.generate.call_args
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_string_urls_normalized():
    """Plain string URLs in the list should still be accepted."""
    action = _make_model_action("desc")
    result = await generate_image_interpretation(
        ["https://example.com/pic.png"], action
    )
    assert result == "desc"
    action.create_multimodal_content.assert_called_once()


@pytest.mark.asyncio
async def test_strips_whitespace_from_result():
    action = _make_model_action("  padded result  ")
    result = await generate_image_interpretation(
        [{"url": "https://x.com/a.jpg"}], action
    )
    assert result == "padded result"
