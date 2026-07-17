"""Model retry Retry-After handling + HTTP client loop safety (AUDIT-actions LOW)."""

from __future__ import annotations

import httpx
import pytest

from jvagent.action.model.base import BaseModelAction

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
