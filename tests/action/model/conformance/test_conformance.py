"""Provider conformance: every adapter, the same logical exchange, the same
normalised ``ModelResponse``.

Parametrised over (provider × scenario). Responses replay from fixtures
(recorded when present, authored otherwise); set ``JVAGENT_CONFORMANCE_RECORD=1``
with a provider key in the environment to re-record a provider's fixtures
against its real endpoint.

Phase 1 of ``.planning/specs/2026-09-05-model-integration-remediation.md``: this
is the instrument that makes every later adapter change safe — a provider quirk
becomes a failing recorded fixture, not a production incident.
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest

from jvagent.action.model.contract import FinishReason, ModelRequest, ModelResponse
from tests.action.model.conformance._transport import save_recorded
from tests.action.model.conformance.authored import SCENARIOS
from tests.action.model.conformance.conftest import PROVIDERS, make_case

pytestmark = pytest.mark.asyncio

_CASES = [
    pytest.param(provider, scenario, id=f"{provider}-{scenario}")
    for provider in PROVIDERS
    for scenario in SCENARIOS
]


def _tool_shape(calls: List[Any]) -> List[Dict[str, Any]]:
    return [{"name": c.name, "arguments": c.arguments} for c in calls]


def _has_tool_result(provider: str, body: Dict[str, Any], call_id: str) -> bool:
    """Did the adapter send the tool result back in the provider's own shape?"""
    messages = body.get("messages") or []
    if provider == "anthropic":
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id") == call_id
                    ):
                        return True
        return False
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return True
    return False


async def _run(case, scenario: Dict[str, Any]) -> ModelResponse:
    request = ModelRequest(**scenario["request"])
    for key, value in (scenario.get("action") or {}).items():
        setattr(case.action, key, value)
    return await case.action.complete(request)


@pytest.mark.parametrize("provider,scenario_name", _CASES)
async def test_adapter_conforms(provider: str, scenario_name: str, monkeypatch):
    scenario = SCENARIOS[scenario_name]
    expect = scenario["expect"]
    case = make_case(provider, scenario_name, monkeypatch)
    if case is None:
        pytest.skip(f"recording requested but no key for {provider}")

    try:
        if expect.get("error"):
            with pytest.raises(Exception) as excinfo:
                await _run(case, scenario)
            if expect["error"] == "HTTPStatusError":
                # httpx raises HTTPStatusError; SDK-style adapters (LiteLLM)
                # raise their own exception carrying the status code.
                exc = excinfo.value
                assert isinstance(exc, httpx.HTTPStatusError) or (
                    getattr(exc, "status_code", None) == 500
                ), type(exc)
            response = None
        else:
            response = await _run(case, scenario)
    finally:
        if case.recording:
            fixture = dict(case.fixture)
            fixture["responses"] = case.transport.captured
            save_recorded(provider, scenario_name, fixture)
        client = getattr(case.action, "_http_client", None)
        if client is not None:
            await client.aclose()

    if response is None:
        return

    # Provider label is the adapter's, whatever wire it speaks.
    assert response.provider == provider
    assert response.model

    if "text" in expect:
        assert response.text.strip() == expect["text"]
    if "finish_reason" in expect:
        assert response.finish_reason == expect["finish_reason"]
        assert response.finish_reason in vars(FinishReason).values()
    if "tool_calls" in expect:
        assert _tool_shape(response.tool_calls) == expect["tool_calls"]
        for call in response.tool_calls:
            assert call.id, "every tool call needs an id for transcript replay"
    if expect.get("thinking"):
        assert response.thinking.strip()
    if "usage" in expect and not scenario["request"].get("stream"):
        for key, value in expect["usage"].items():
            assert getattr(response.usage, key) == value, key
        assert response.usage.total_tokens == (
            response.usage.prompt_tokens + response.usage.completion_tokens
        )
        assert response.usage.estimated is False
    if "cached_read_tokens" in expect:
        wire = "openai" if provider in ("groq", "openrouter", "litellm") else provider
        assert response.usage.cached_read_tokens == expect["cached_read_tokens"][wire]
        assert response.usage.cached_read_tokens <= response.usage.prompt_tokens
    if "request_has_tool_result" in expect:
        assert _has_tool_result(
            provider, case.request_json(), expect["request_has_tool_result"]
        )
    if "requests" in expect:
        assert case.request_count == expect["requests"]


async def test_scenario_matrix_is_complete():
    """Every provider must have an authored body for every scenario, so a new
    scenario cannot silently skip a provider."""
    from tests.action.model.conformance.authored import BODIES, WIRE_FOR

    for provider in PROVIDERS:
        wire = WIRE_FOR[provider]
        missing = sorted(set(SCENARIOS) - set(BODIES[wire]))
        assert not missing, f"{provider} lacks bodies for {missing}"
