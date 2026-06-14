"""Streaming tool-call accumulation for OpenAI and Anthropic language model actions."""

import json
from typing import Any, Dict, List

import httpx
import pytest

from jvagent.action.model.language.anthropic.anthropic import (
    AnthropicLanguageModelAction,
)
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction


class _MockStreamResponse:
    def __init__(self, lines: List[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://localhost")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

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
    def __init__(self, stream_response: _MockStreamResponse):
        self._stream_response = stream_response

    def stream(self, *args, **kwargs):
        return _MockStreamContext(self._stream_response)


@pytest.mark.asyncio
async def test_openai_query_stream_merges_tool_call_fragments(monkeypatch):
    """Delta.tool_calls arrive in multiple SSE chunks; merge by index."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    action = OpenAILanguageModelAction()

    def sse_chunk(delta: Dict[str, Any]) -> str:
        return "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n"

    lines = [
        sse_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_merge",
                        "type": "function",
                        "function": {"name": "read_skill", "arguments": ""},
                    }
                ]
            }
        ),
        sse_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": '{"skill_name":'},
                    }
                ]
            }
        ),
        sse_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": ' "answer"}'},
                    }
                ]
            }
        ),
        "data: [DONE]\n",
    ]

    action._http_client = _MockHttpClient(_MockStreamResponse(lines))

    result = await action.query_stream("x")
    async for _ in result.iter_stream():
        pass

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["id"] == "call_merge"
    assert tc["function"]["name"] == "read_skill"
    args = json.loads(tc["function"]["arguments"])
    assert args == {"skill_name": "answer"}


@pytest.mark.asyncio
async def test_anthropic_query_stream_assembles_tool_use_from_blocks(monkeypatch):
    """tool_use input is streamed via input_json_delta; message_stop has no message body."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    action = AnthropicLanguageModelAction()

    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "read_skill",
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"skill_name":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"answer"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    lines = ["data: " + json.dumps(e) + "\n" for e in events]

    action._http_client = _MockHttpClient(_MockStreamResponse(lines))

    result = await action.query_stream("x")
    async for _ in result.iter_stream():
        pass

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["function"]["name"] == "read_skill"
    assert json.loads(tc["function"]["arguments"]) == {"skill_name": "answer"}
