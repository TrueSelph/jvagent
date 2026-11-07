"""Model retry Retry-After handling + HTTP client loop safety (AUDIT-actions LOW)."""

from __future__ import annotations

import httpx
import pytest

from jvagent.action.model.base import BaseModelAction
from jvagent.action.model.context import bind_model_attempt_guard, set_interaction

pytestmark = pytest.mark.asyncio


class _Stub(BaseModelAction):
    pass


def _429(retry_after: str) -> httpx.HTTPStatusError:
    resp = httpx.Response(429, headers={"Retry-After": retry_after})
    return httpx.HTTPStatusError(
        "429", request=httpx.Request("GET", "http://x"), response=resp
    )


async def test_retry_after_honored_above_retry_max_delay():
    a = _Stub()  # retry_max_delay=20, retry_after_max=300
    # A 60s Retry-After must be honored — NOT clamped to retry_max_delay (20).
    delay = a._compute_retry_delay_seconds(0, _429("60"))
    assert delay == 60.0


async def test_retry_after_capped_at_retry_after_max():
    a = _Stub()
    delay = a._compute_retry_delay_seconds(0, _429("999999"))
    assert delay == 300.0  # bounded by retry_after_max


async def test_normal_backoff_still_bounded_by_retry_max_delay():
    a = _Stub()
    a.retry_jitter = False
    # No Retry-After header → exponential backoff, capped at retry_max_delay.
    resp = httpx.Response(500)
    exc = httpx.HTTPStatusError(
        "500", request=httpx.Request("GET", "http://x"), response=resp
    )
    delay = a._compute_retry_delay_seconds(10, exc)  # huge attempt
    assert delay == 20.0


async def test_http_client_reused_on_same_loop():
    a = _Stub()
    await a._initialize_http_client()
    c1 = a._http_client
    assert c1 is not None
    await a._initialize_http_client()
    assert a._http_client is c1  # reused


async def test_http_client_recreated_on_loop_change():
    a = _Stub()
    await a._initialize_http_client()
    c1 = a._http_client

    # Simulate the cached client having been created on a different (now closed)
    # event loop — as on a serverless warm start.
    a._http_client_loop_id = -12345

    await a._initialize_http_client()
    c2 = a._http_client
    assert c2 is not None
    assert c2 is not c1  # recreated for the current loop


async def test_failed_transport_attempt_is_recorded_without_fake_usage():
    events = []

    class _Interaction:
        observability_metrics = events

        async def save(self):
            return None

    action = _Stub()
    action.max_retries = 0
    set_interaction(_Interaction())
    error = httpx.HTTPStatusError(
        "unauthorized",
        request=httpx.Request("POST", "https://provider.invalid"),
        response=httpx.Response(401),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await action._execute_with_retry(
                lambda: _raise(error), op_name="completion"
            )
    finally:
        set_interaction(None)

    assert len(events) == 1
    assert events[0]["event_type"] == "model_attempt"
    assert events[0]["data"]["outcome"] == "failed"
    assert events[0]["data"]["status_code"] == 401
    assert events[0]["data"]["usage_status"] == "provider_unreported"
    assert "usage" not in events[0]["data"]
    assert "cost_usd" not in events[0]["data"]


async def test_retry_records_each_transport_attempt():
    events = []

    class _Interaction:
        observability_metrics = events

        async def save(self):
            return None

    action = _Stub()
    action.max_retries = 1
    action.retry_initial_delay = 0
    action.retry_jitter = False
    set_interaction(_Interaction())
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary")
        return "ok"

    try:
        result = await action._execute_with_retry(
            lambda: operation(), op_name="completion"
        )
    finally:
        set_interaction(None)

    assert result == "ok"
    assert [event["data"]["outcome"] for event in events] == ["failed", "succeeded"]
    assert [event["data"]["attempt_number"] for event in events] == [1, 2]


async def test_task_local_attempt_guard_stops_retry_before_second_wire_attempt():
    action = _Stub()
    action.max_retries = 1
    action.retry_initial_delay = 0
    action.retry_jitter = False
    guard_calls = 0
    operation_calls = 0

    async def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise RuntimeError("budget exhausted")

    async def operation():
        nonlocal operation_calls
        operation_calls += 1
        raise httpx.ConnectError("temporary")

    with bind_model_attempt_guard(guard):
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await action._execute_with_retry(lambda: operation(), op_name="completion")

    assert guard_calls == 2
    assert operation_calls == 1


async def test_success_telemetry_uses_provider_neutral_zero_cost_record():
    from jvagent.action.model.language.base import ModelActionResult

    events = []

    class _Interaction:
        observability_metrics = events

        async def save(self):
            return None

    class _OpenAIStub(_Stub):
        provider: str = "openai"

    action = _OpenAIStub()
    action.model = "gpt-4o-mini"
    result = ModelActionResult(
        response="ok",
        usage={"prompt_tokens": 10, "completion_tokens": 2},
        model="gpt-4o-mini",
        provider="openai",
    )
    result.metrics["cost_usd"] = 0.0
    result.metrics["cost_source"] = "provider_zero_cost_receipt"
    set_interaction(_Interaction())
    try:
        await action.track_usage(
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            result=result,
        )
    finally:
        set_interaction(None)

    event = next(item for item in events if item["event_type"] == "model_call")
    assert event["data"]["cost_record"] == {
        "amount": 0.0,
        "currency": "USD",
        "source": "provider_zero_cost_receipt",
        "estimated": False,
        "pricing_version": None,
    }


async def _raise(error):
    raise error
