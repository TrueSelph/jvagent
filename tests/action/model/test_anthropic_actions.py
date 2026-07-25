"""Tests for Anthropic language model action."""

import json
from typing import Any, Dict, List, Optional

import httpx
import pytest

from jvagent.action.model import AnthropicLanguageModelAction


class _MockResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(
                    self.status_code, request=self.request, json=self._payload
                ),
            )

    def json(self) -> Dict[str, Any]:
        return self._payload


class _MockStreamResponse(_MockResponse):
    def __init__(self, lines: List[str], status_code: int = 200):
        super().__init__({}, status_code=status_code)
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _MockStreamContext:
    def __init__(self, response: _MockStreamResponse):
        self._response = response

    async def __aenter__(self) -> _MockStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _MockHttpClient:
    def __init__(
        self,
        post_response: Any = None,
        stream_response: Optional[_MockStreamResponse] = None,
        post_exception: Exception = None,
    ):
        self._post_response = post_response
        self._stream_response = stream_response
        self._post_exception = post_exception
        self.last_post_json: Optional[Dict[str, Any]] = None
        self.last_post_headers: Optional[Dict[str, Any]] = None

    async def post(self, *args, **kwargs):
        self.last_post_json = kwargs.get("json")
        self.last_post_headers = kwargs.get("headers")
        if self._post_exception is not None:
            raise self._post_exception
        return self._post_response

    def stream(self, *args, **kwargs):
        return _MockStreamContext(self._stream_response)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_anthropic_lm_query_sync_parses_response_and_usage():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "content": [{"type": "text", "text": "Hello from Claude"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        )
    )

    result = await action.query_sync("Say hello")

    assert await result.get_response() == "Hello from Claude"
    assert result.provider == "anthropic"
    assert result.finish_reason == "end_turn"
    assert result.metrics["prompt_tokens"] == 12
    assert result.metrics["completion_tokens"] == 8
    assert result.metrics["total_tokens"] == 20


@pytest.mark.asyncio
async def test_anthropic_lm_query_sync_maps_tools_and_system():
    action = AnthropicLanguageModelAction()
    client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "content": [],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        )
    )
    action._http_client = client

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]

    await action.query_sync("Weather?", system="You are precise.", tools=tools)

    assert client.last_post_json is not None
    assert client.last_post_json["system"] == "You are precise."
    assert client.last_post_json["tools"][0]["name"] == "get_weather"
    assert client.last_post_json["tools"][0]["input_schema"]["required"] == ["city"]


@pytest.mark.asyncio
async def test_anthropic_lm_query_sync_maps_multimodal_data_url():
    action = AnthropicLanguageModelAction()
    client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "content": [{"type": "text", "text": "Looks like a map."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 3},
            }
        )
    )
    action._http_client = client

    prompt = action.create_image_content(
        text="Describe this image",
        image_base64="ZmFrZV9pbWFnZV9kYXRh",
    )

    await action.query_sync(prompt)

    assert client.last_post_json is not None
    content_blocks = client.last_post_json["messages"][0]["content"]
    image_blocks = [b for b in content_blocks if b.get("type") == "image"]
    assert image_blocks
    assert image_blocks[0]["source"]["type"] == "base64"
    assert image_blocks[0]["source"]["data"] == "ZmFrZV9pbWFnZV9kYXRh"


@pytest.mark.asyncio
async def test_anthropic_lm_query_stream_parses_chunks_and_usage():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        stream_response=_MockStreamResponse(
            [
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Claude"}}',
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
                'data: {"type":"message_stop","message":{"content":[],"usage":{"input_tokens":9,"output_tokens":6}}}',
            ]
        )
    )

    result = await action.query_stream("Say hello")
    chunks = []
    async for chunk in result.iter_stream():
        chunks.append(chunk)

    assert "".join(chunks) == "Hello Claude"
    assert result.finish_reason == "end_turn"
    assert result.metrics["prompt_tokens"] == 9
    assert result.metrics["completion_tokens"] == 6
    assert result.metrics["total_tokens"] == 15


@pytest.mark.asyncio
async def test_anthropic_lm_query_sync_parses_tool_calls():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {"city": "Austin"},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
    )

    result = await action.query_sync("Use tool")

    assert result.finish_reason == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "get_weather"
    assert result.tool_calls[0]["function"]["arguments"] == '{"city": "Austin"}'


@pytest.mark.asyncio
async def test_anthropic_lm_query_sync_normalizes_openai_tool_messages():
    action = AnthropicLanguageModelAction()
    client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        )
    )
    action._http_client = client
    messages = [
        {"role": "user", "content": "Run a tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "/tmp/data.txt"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
    ]

    await action.query_messages(messages=messages)
    assert client.last_post_json is not None
    payload_messages = client.last_post_json["messages"]
    assert payload_messages[1]["role"] == "assistant"
    assert payload_messages[1]["content"][0]["type"] == "tool_use"
    assert payload_messages[2]["role"] == "user"
    assert payload_messages[2]["content"][0]["type"] == "tool_result"


def test_anthropic_headers_use_env_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-token")
    action = AnthropicLanguageModelAction()

    headers = action._headers()
    assert headers["x-api-key"] == "env-token"


@pytest.mark.asyncio
async def test_anthropic_lm_query_http_error_raises():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse({"error": "bad request"}, status_code=400)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await action.query_sync("fail")


@pytest.mark.asyncio
async def test_anthropic_lm_query_request_error_raises():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_exception=httpx.RequestError(
            "network",
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )
    )

    with pytest.raises(httpx.RequestError):
        await action.query_sync("fail")


def test_anthropic_exports_available():
    from jvagent.action.model import AnthropicLanguageModelAction as TopLevelExport
    from jvagent.action.model.language import AnthropicLanguageModelAction as LmExport

    assert TopLevelExport is AnthropicLanguageModelAction
    assert LmExport is AnthropicLanguageModelAction


# --- prompt caching --------------------------------------------------------
#
# Unlike OpenAI's automatic prefix caching, Anthropic caches only up to an
# explicit ``cache_control`` breakpoint. An agentic caller resends a large,
# stable system prompt on every tick, so the breakpoint is what makes those
# resends cheap.


def _big_system(n: int = 6000) -> str:
    return "You are precise. " * (n // 17)


@pytest.mark.asyncio
async def test_large_system_prompt_gets_a_cache_breakpoint():
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {"content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
    )
    system = _big_system()

    await action.query_sync("hi", system=system)

    blocks = action._http_client.last_post_json["system"]
    assert isinstance(blocks, list)
    assert blocks[0]["text"] == system
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_small_system_prompt_is_left_as_a_plain_string():
    """Below the provider's cacheable minimum a breakpoint buys nothing, so the
    payload shape stays exactly as it was."""
    action = AnthropicLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {"content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
    )

    await action.query_sync("hi", system="You are precise.")

    assert action._http_client.last_post_json["system"] == "You are precise."


@pytest.mark.asyncio
async def test_prompt_caching_can_be_switched_off():
    action = AnthropicLanguageModelAction()
    action.prompt_caching = False
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {"content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
    )
    system = _big_system()

    await action.query_sync("hi", system=system)

    assert action._http_client.last_post_json["system"] == system


def test_cached_input_is_folded_into_prompt_tokens_and_kept_visible():
    """Anthropic reports cached input separately and excludes it from
    ``input_tokens``; totals must stay comparable across providers while the
    split stays observable."""
    action = AnthropicLanguageModelAction()
    usage = action._extract_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 3000,
                "cache_creation_input_tokens": 400,
            }
        }
    )
    assert usage["prompt_tokens"] == 3500
    assert usage["total_tokens"] == 3520
    assert usage["cache_read_input_tokens"] == 3000
    assert usage["cache_creation_input_tokens"] == 400
