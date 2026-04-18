"""Tests for OpenRouter language model action behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction
from jvagent.action.model.language.openrouter.openrouter import (
    OpenRouterLanguageModelAction,
)


@pytest.mark.asyncio
async def test_openrouter_query_passes_extra_headers_per_request():
    action = OpenRouterLanguageModelAction()
    action.http_referer = "https://example.com"
    action.site_name = "jvagent-tests"

    result = ModelActionResult(response="ok", usage={}, model="x", provider="openai")
    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(return_value=result)
    ) as mocked_query:
        out = await action._query(
            messages=[{"role": "user", "content": "hi"}], tools=None
        )

    assert out.provider == "openrouter"
    extra_headers = mocked_query.await_args.kwargs["_extra_headers"]
    assert extra_headers["HTTP-Referer"] == "https://example.com"
    assert extra_headers["X-Title"] == "jvagent-tests"


@pytest.mark.asyncio
async def test_openrouter_query_stream_passes_extra_headers_per_request():
    action = OpenRouterLanguageModelAction()
    action.http_referer = "https://example.com"
    action.site_name = "jvagent-tests"

    result = ModelActionResult(response="ok", usage={}, model="x", provider="openai")
    result._messages_for_estimation = [{"role": "user", "content": "hi"}]
    with patch.object(
        OpenAILanguageModelAction, "_query_stream", AsyncMock(return_value=result)
    ) as mocked_query_stream:
        out = await action._query_stream(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
        )

    assert out.provider == "openrouter"
    extra_headers = mocked_query_stream.await_args.kwargs["_extra_headers"]
    assert extra_headers["HTTP-Referer"] == "https://example.com"
    assert extra_headers["X-Title"] == "jvagent-tests"
