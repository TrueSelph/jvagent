"""Tests for Ollama language and embedding model actions."""

import json
from typing import Any, Dict, List

import httpx
import pytest

from jvagent.action.model import (
    OllamaEmbeddingModelAction,
    OllamaLanguageModelAction,
)


class _MockResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "http://localhost")

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
        stream_response: _MockStreamResponse = None,
        post_exception: Exception = None,
    ):
        self._post_response = post_response
        self._stream_response = stream_response
        self._post_exception = post_exception

    async def post(self, *args, **kwargs):
        if self._post_exception is not None:
            raise self._post_exception
        return self._post_response

    def stream(self, *args, **kwargs):
        return _MockStreamContext(self._stream_response)


@pytest.mark.asyncio
async def test_ollama_lm_query_sync_parses_response():
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "message": {"content": "Hello from Ollama", "tool_calls": []},
                "done_reason": "stop",
                "prompt_eval_count": 7,
                "eval_count": 11,
            }
        )
    )

    result = await action.query_sync("Say hello")

    assert await result.get_response() == "Hello from Ollama"
    assert result.provider == "ollama"
    assert result.finish_reason == "stop"
    assert result.metrics["prompt_tokens"] == 7
    assert result.metrics["completion_tokens"] == 11
    assert result.metrics["total_tokens"] == 18


@pytest.mark.asyncio
async def test_ollama_lm_query_sync_normalizes_tool_calls():
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "/tmp/test.txt"},
                            }
                        }
                    ],
                },
                "done_reason": "tool_calls",
                "prompt_eval_count": 7,
                "eval_count": 11,
            }
        )
    )

    result = await action.query_sync("Use a tool")
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    assert call["function"]["arguments"] == '{"path": "/tmp/test.txt"}'


@pytest.mark.asyncio
async def test_ollama_lm_query_stream_parses_chunks_and_usage():
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        stream_response=_MockStreamResponse(
            [
                json.dumps({"message": {"content": "Hello "}, "done": False}),
                json.dumps(
                    {
                        "message": {"content": "world"},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "message": {"content": "", "tool_calls": []},
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 4,
                        "eval_count": 5,
                    }
                ),
            ]
        )
    )

    result = await action.query_stream("Say hello")
    chunks = []
    async for chunk in result.iter_stream():
        chunks.append(chunk)

    assert "".join(chunks) == "Hello world"
    assert result.finish_reason == "stop"
    assert result.metrics["total_tokens"] > 0


@pytest.mark.asyncio
async def test_ollama_lm_query_stream_normalizes_tool_calls():
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        stream_response=_MockStreamResponse(
            [
                json.dumps({"message": {"content": ""}, "done": False}),
                json.dumps(
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "/tmp/from_stream.txt"},
                                    }
                                }
                            ],
                        },
                        "done": True,
                        "done_reason": "tool_calls",
                        "prompt_eval_count": 4,
                        "eval_count": 5,
                    }
                ),
            ]
        )
    )

    result = await action.query_stream("Use tool")
    async for _ in result.iter_stream():
        pass

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    assert call["function"]["arguments"] == '{"path": "/tmp/from_stream.txt"}'


@pytest.mark.asyncio
async def test_ollama_lm_query_stream_accumulates_tool_calls_from_early_chunks():
    """Tool calls may appear before the final done chunk (e.g. kimi via Ollama Cloud)."""
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        stream_response=_MockStreamResponse(
            [
                json.dumps(
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_early",
                                    "function": {
                                        "name": "read_skill",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_early",
                                    "function": {
                                        "name": "read_skill",
                                        "arguments": '{"skill_name": "answer"}',
                                    },
                                }
                            ],
                        },
                        "done": True,
                        "done_reason": "tool_calls",
                        "prompt_eval_count": 4,
                        "eval_count": 5,
                    }
                ),
            ]
        )
    )

    result = await action.query_stream("Use skill")
    async for _ in result.iter_stream():
        pass

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["function"]["name"] == "read_skill"
    assert '"skill_name": "answer"' in call["function"]["arguments"]


@pytest.mark.asyncio
async def test_ollama_embedding_parses_vector_and_dimensions():
    action = OllamaEmbeddingModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse({"embeddings": [[0.1, 0.2, 0.3]]})
    )

    vector = await action.embed("embed this")

    assert vector == [0.1, 0.2, 0.3]
    assert action.embedding_dimensions == 3


@pytest.mark.asyncio
async def test_ollama_embedding_batch_parses_vectors():
    action = OllamaEmbeddingModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    )

    vectors = await action.embed_batch(["one", "two"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_ollama_lm_query_http_error_raises():
    action = OllamaLanguageModelAction()
    action._http_client = _MockHttpClient(
        post_response=_MockResponse({"error": "bad request"}, status_code=400)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await action.query_sync("fail")


@pytest.mark.asyncio
async def test_ollama_embedding_request_error_raises_runtime_error():
    action = OllamaEmbeddingModelAction()
    action._http_client = _MockHttpClient(
        post_exception=httpx.RequestError(
            "network",
            request=httpx.Request("POST", "http://localhost/api/embed"),
        )
    )

    with pytest.raises(RuntimeError):
        await action.embed("embed this")


def test_ollama_host_root_strips_docs_style_api_suffix():
    from jvagent.action.model.ollama_endpoint import ollama_host_root

    assert ollama_host_root("https://ollama.com/api") == "https://ollama.com"
    assert ollama_host_root("http://localhost:11434/api/") == "http://localhost:11434"
    assert ollama_host_root("http://localhost:11434") == "http://localhost:11434"


def test_ollama_exports_available():
    from jvagent.action.model.embedding import OllamaEmbeddingModelAction as EmbExport
    from jvagent.action.model.language import OllamaLanguageModelAction as LmExport

    assert EmbExport is OllamaEmbeddingModelAction
    assert LmExport is OllamaLanguageModelAction
