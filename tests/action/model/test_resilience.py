"""Resilience primitives (ADR-0046): circuit breaker, fallback candidates,
retry deadline and timeout retry toggle."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from jvagent.action.model import resilience
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction
from jvagent.action.model.resilience import (
    CircuitBreaker,
    breaker_key,
    fallback_candidates,
)


class _Action:
    def __init__(self, name, model="m"):
        self._name = name
        self.model = model

    def get_class_name(self):
        return self._name


# --- breaker -------------------------------------------------------------------


def test_breaker_opens_after_threshold_and_half_opens_after_cooldown():
    import time

    cb = CircuitBreaker(threshold=2, cooldown_seconds=10)
    key = breaker_key(_Action("OpenAI"), "gpt")
    assert key == "OpenAI:gpt"
    t0 = time.monotonic()
    assert cb.record_failure(key, "boom", now=t0) is False
    assert cb.is_open(key, now=t0) is False
    assert cb.record_failure(key, "boom again", now=t0 + 1) is True
    assert cb.is_open(key, now=t0 + 5) is True
    snap = cb.snapshot()[key]
    assert snap["open"] is True and snap["trips"] == 1
    assert "boom again" in snap["last_error"] and snap["cooldown_remaining_s"] > 0
    # cooldown elapsed → half-open: one probe passes
    assert cb.is_open(key, now=t0 + 11) is False
    cb.record_success(key)
    assert cb.is_open(key, now=t0 + 12) is False
    assert cb.snapshot()[key]["failures"] == 0


def test_breaker_disabled_at_threshold_zero_and_isolated_per_loop(monkeypatch):
    cb = CircuitBreaker(threshold=0)
    assert cb.record_failure("k", "x") is False and cb.is_open("k") is False

    cb = CircuitBreaker(threshold=1, cooldown_seconds=60)
    monkeypatch.setattr(resilience, "_loop_id", lambda: 1)
    cb.record_failure("k", "x")
    assert cb.is_open("k") is True
    monkeypatch.setattr(resilience, "_loop_id", lambda: 2)
    assert cb.is_open("k") is False  # a fresh loop starts with closed circuits


# --- fallback candidates -----------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_candidates_resolve_entries_and_skip_unresolvable():
    primary = _Action("Primary", "p-model")
    other = _Action("Other", "o-model")

    async def resolve(action_type):
        return other if action_type == "Other" else None

    candidates = await fallback_candidates(
        (primary, "p-model"),
        [
            {"model": "p-fallback"},  # same action, other model
            {"model": "o-model", "model_action_type": "Other"},
            {"model": "ghost", "model_action_type": "Missing"},  # skipped
            "p-bare",  # bare string → primary action
            {},  # ignored
        ],
        resolve,
    )
    assert [(a.get_class_name(), m) for a, m in candidates] == [
        ("Primary", "p-model"),
        ("Primary", "p-fallback"),
        ("Other", "o-model"),
        ("Primary", "p-bare"),
    ]


# --- retry deadline / timeout toggle ----------------------------------------------


def _timeout():
    return httpx.ReadTimeout("slow")


@pytest.mark.asyncio
async def test_retry_deadline_stops_retrying_when_the_next_delay_would_exceed_it():
    action = OpenAILanguageModelAction()
    action.max_retries = 5
    action.retry_jitter = False
    action.retry_initial_delay = 100.0  # every retry would wait 100s
    action.retry_max_delay = 100.0
    action.retry_total_deadline_seconds = 30.0
    calls = {"n": 0}

    async def fake_query(*args, **kwargs):
        calls["n"] += 1
        raise _timeout()

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=fake_query)
    ):
        with patch("jvagent.action.model.base.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.ReadTimeout):
                await action.query_messages(
                    messages=[{"role": "user", "content": "hi"}], stream=False
                )
    assert calls["n"] == 1  # no retry started past the deadline


@pytest.mark.asyncio
async def test_retry_deadline_zero_keeps_the_unbounded_behaviour():
    action = OpenAILanguageModelAction()
    action.max_retries = 2
    action.retry_jitter = False
    action.retry_initial_delay = 100.0
    action.retry_max_delay = 100.0
    action.retry_total_deadline_seconds = 0.0
    calls = {"n": 0}

    async def fake_query(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _timeout()
        return ModelActionResult(response="ok", model="m", provider="openai")

    with patch.object(
        OpenAILanguageModelAction, "_query", AsyncMock(side_effect=fake_query)
    ):
        with patch("jvagent.action.model.base.asyncio.sleep", new_callable=AsyncMock):
            result = await action.query_messages(
                messages=[{"role": "user", "content": "hi"}], stream=False
            )
    assert calls["n"] == 3 and await result.get_response() == "ok"


def test_timeouts_are_not_retried_when_the_operator_turns_it_off():
    action = OpenAILanguageModelAction()
    assert action._is_retryable_exception(_timeout()) is True
    action.retry_on_timeout = False
    assert action._is_retryable_exception(_timeout()) is False
    # transport errors and retryable statuses are unaffected
    assert action._is_retryable_exception(httpx.ConnectError("x")) is True

    class _SdkError(Exception):
        status_code = 503

    assert action._is_retryable_exception(_SdkError()) is True
