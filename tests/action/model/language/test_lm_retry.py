"""Tests for language model HTTP retry behavior (BaseModelAction + query_messages)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction


def _http_401_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError("Unauthorized", request=request, response=response)


def _http_429_error(retry_after: str = "5") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": retry_after},
    )
    return httpx.HTTPStatusError("Too Many", request=request, response=response)


@pytest.mark.asyncio
async def test_sync_retry_succeeds_after_read_timeouts():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    action.retry_jitter = False
    action.retry_initial_delay = 0.01
    calls = {"n": 0}

    async def fake_query(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ReadTimeout("timeout")
        return ModelActionResult(
            response="ok",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model="gpt-4o-mini",
            provider="openai",
        )

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=fake_query)
    ):
        with patch("jvagent.action.model.base.asyncio.sleep", new_callable=AsyncMock):
            result = await action.query_messages(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )

    assert calls["n"] == 3
    assert await result.get_response() == "ok"


@pytest.mark.asyncio
async def test_sync_retry_exhausted():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    action.retry_jitter = False
    action.retry_initial_delay = 0.01
    calls = {"n": 0}

    async def always_timeout(*args, **kwargs):
        calls["n"] += 1
        raise httpx.ReadTimeout("timeout")

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=always_timeout)
    ):
        with patch("jvagent.action.model.base.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.ReadTimeout):
                await action.query_messages(
                    messages=[{"role": "user", "content": "hi"}],
                    stream=False,
                )

    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_sync_no_retry_on_401():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    calls = {"n": 0}

    async def raise_401(*args, **kwargs):
        calls["n"] += 1
        raise _http_401_error()

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=raise_401)
    ):
        with pytest.raises(httpx.HTTPStatusError) as ei:
            await action.query_messages(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )

    assert calls["n"] == 1
    assert ei.value.response.status_code == 401


@pytest.mark.asyncio
async def test_sync_429_uses_retry_after_header():
    action = OpenAILanguageModelAction()
    action.max_retries = 1
    action.retry_jitter = False
    action.retry_max_delay = 100.0
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fail_then_ok(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_429_error("7")
        return ModelActionResult(
            response="ok",
            usage={},
            model="gpt-4o-mini",
            provider="openai",
        )

    async def capture_sleep(delay: float) -> None:
        sleeps.append(delay)

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=fail_then_ok)
    ):
        with patch(
            "jvagent.action.model.base.asyncio.sleep", side_effect=capture_sleep
        ):
            result = await action.query_messages(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )

    assert await result.get_response() == "ok"
    assert calls["n"] == 2
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_stream_retries_before_first_chunk():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    action.retry_jitter = False
    action.retry_initial_delay = 0.01
    stream_calls = {"n": 0}

    async def fake_query_stream(*args, **kwargs):
        stream_calls["n"] += 1
        if stream_calls["n"] == 1:

            async def bad():
                raise httpx.ReadTimeout("t")
                yield ""  # pragma: no cover

            return ModelActionResult(
                stream=bad(),
                usage={},
                model="gpt-4o-mini",
                provider="openai",
            )

        async def good():
            yield "a"
            yield "b"

        return ModelActionResult(
            stream=good(),
            usage={},
            model="gpt-4o-mini",
            provider="openai",
        )

    with patch.object(
        OpenAILanguageModelAction,
        "_query_stream",
        AsyncMock(side_effect=fake_query_stream),
    ):
        with patch(
            "jvagent.action.model.language.base.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await action.query_messages(
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )

            chunks: list[str] = []
            async for c in result.iter_stream():
                chunks.append(c)

    assert stream_calls["n"] == 2
    assert "".join(chunks) == "ab"


@pytest.mark.asyncio
async def test_stream_no_retry_after_first_chunk():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    action.retry_jitter = False
    stream_calls = {"n": 0}

    async def fake_query_stream(*args, **kwargs):
        stream_calls["n"] += 1

        async def mid_fail():
            yield "x"
            raise httpx.ReadTimeout("mid")

        return ModelActionResult(
            stream=mid_fail(),
            usage={},
            model="gpt-4o-mini",
            provider="openai",
        )

    with patch.object(
        OpenAILanguageModelAction,
        "_query_stream",
        AsyncMock(side_effect=fake_query_stream),
    ):
        result = await action.query_messages(
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )

        chunks: list[str] = []
        with pytest.raises(httpx.ReadTimeout):
            async for c in result.iter_stream():
                chunks.append(c)

    assert chunks == ["x"]
    assert stream_calls["n"] == 1
