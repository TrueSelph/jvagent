"""Tests for OpenAI reasoning-model Chat Completions payload shaping."""

import json
from typing import Any, Dict

import httpx
import pytest

from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction
from jvagent.action.model.language.openrouter.openrouter import (
    OpenRouterLanguageModelAction,
)


def test_stream_payload_includes_usage_stream_options():
    action = OpenAILanguageModelAction()
    payload = action._build_openai_payload(
        [{"role": "user", "content": "hi"}],
        None,
        stream=True,
        model="gpt-4o-mini",
    )
    assert payload.get("stream") is True
    assert payload.get("stream_options", {}).get("include_usage") is True


def test_matches_reasoning_model_public_api():
    action = OpenAILanguageModelAction()
    assert action.matches_reasoning_model("o3-mini")
    assert not action.matches_reasoning_model("gpt-4o-mini")


def _success_chat_response(content: str = "hi") -> Dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


@pytest.mark.asyncio
async def test_reasoning_model_payload():
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_chat_response())

    transport = httpx.MockTransport(handler)
    action = OpenAILanguageModelAction()
    action.api_key = "sk-test"
    async with httpx.AsyncClient(transport=transport) as client:
        action._http_client = client
        result = await action.query_messages(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            model="o3-mini",
            temperature=0.7,
            max_tokens=2048,
            top_p=0.9,
            reasoning_effort="medium",
        )

    body = captured["body"]
    assert body["model"] == "o3-mini"
    assert body["max_completion_tokens"] == 2048
    assert "temperature" not in body
    assert "top_p" not in body
    assert "max_tokens" not in body
    assert body.get("reasoning_effort") == "medium"
    assert "reasoning" not in body
    assert await result.get_response() == "hi"


@pytest.mark.asyncio
async def test_reasoning_effort_from_nested_reasoning_kwarg():
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_chat_response())

    transport = httpx.MockTransport(handler)
    action = OpenAILanguageModelAction()
    action.api_key = "sk-test"
    async with httpx.AsyncClient(transport=transport) as client:
        action._http_client = client
        await action.query_messages(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            model="o3-mini",
            max_tokens=512,
            reasoning={"effort": "high"},
        )

    body = captured["body"]
    assert body.get("reasoning_effort") == "high"
    assert "reasoning" not in body


@pytest.mark.asyncio
async def test_non_reasoning_model_unchanged():
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_chat_response())

    transport = httpx.MockTransport(handler)
    action = OpenAILanguageModelAction()
    action.api_key = "sk-test"
    async with httpx.AsyncClient(transport=transport) as client:
        action._http_client = client
        await action.query_messages(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=100,
            top_p=0.95,
        )

    body = captured["body"]
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 100
    assert body["top_p"] == 0.95
    assert "max_completion_tokens" not in body


@pytest.mark.asyncio
async def test_openrouter_reasoning_preserved():
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_chat_response())

    transport = httpx.MockTransport(handler)
    action = OpenRouterLanguageModelAction()
    action.api_key = "sk-test"
    async with httpx.AsyncClient(transport=transport) as client:
        action._http_client = client
        await action.query_messages(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            model="o3-mini",
            temperature=0.5,
            max_tokens=256,
            top_p=0.9,
            reasoning={"effort": "low"},
        )

    body = captured["body"]
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 256
    assert body["reasoning"] == {"effort": "low"}
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body


@pytest.mark.asyncio
async def test_is_reasoning_model_explicit_override():
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_chat_response())

    transport = httpx.MockTransport(handler)
    action = OpenAILanguageModelAction()
    action.api_key = "sk-test"
    async with httpx.AsyncClient(transport=transport) as client:
        action._http_client = client
        await action.query_messages(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            model="custom-vendor-model",
            temperature=0.2,
            max_tokens=128,
            top_p=0.8,
            is_reasoning_model=True,
            reasoning_effort="low",
        )

    body = captured["body"]
    assert body["model"] == "custom-vendor-model"
    assert body["max_completion_tokens"] == 128
    assert body.get("reasoning_effort") == "low"
    assert "temperature" not in body
    assert "top_p" not in body
