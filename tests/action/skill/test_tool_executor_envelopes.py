"""Tests for ToolExecutor observability envelopes (workstream 5)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.tool_executor import ToolExecutionEnvelope, ToolExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(**kwargs) -> ToolExecutor:
    return ToolExecutor(call_timeout=5.0, sanitize_errors=False, **kwargs)


def _make_tool_call(name: str, args: str = "{}", call_id: str = "c1"):
    return {"id": call_id, "function": {"name": name, "arguments": args}}


# ---------------------------------------------------------------------------
# ToolExecutionEnvelope
# ---------------------------------------------------------------------------


class TestToolExecutionEnvelope:
    def test_close_on_success(self):
        env = ToolExecutionEnvelope(
            attempt_id="abc",
            tool_name="search",
            tool_call_id="c1",
            input_fingerprint="ff00",
            start_ts=0.0,
        )
        import time

        env.start_ts = time.monotonic()
        env.close(content="some result", is_error=False)
        assert env.is_error is False
        assert env.latency_ms >= 0
        assert env.content_length == len("some result")
        assert env.error_class == ""

    def test_close_on_failure(self):
        import time

        env = ToolExecutionEnvelope(
            attempt_id="xyz",
            tool_name="tool",
            tool_call_id="c2",
            input_fingerprint="aabb",
            start_ts=time.monotonic(),
        )
        exc = RuntimeError("oops")
        env.close(content="", is_error=True, exc=exc)
        assert env.is_error is True
        assert env.error_class == "RuntimeError"

    def test_recoverable_detection_permanent(self):
        import time

        env = ToolExecutionEnvelope(
            attempt_id="x",
            tool_name="t",
            tool_call_id="c",
            input_fingerprint="00",
            start_ts=time.monotonic(),
        )
        env.close(content="", is_error=True, exc=Exception("permission denied here"))
        assert env.recoverable is False

    def test_recoverable_detection_transient(self):
        import time

        env = ToolExecutionEnvelope(
            attempt_id="x",
            tool_name="t",
            tool_call_id="c",
            input_fingerprint="00",
            start_ts=time.monotonic(),
        )
        env.close(content="", is_error=True, exc=Exception("temporary network error"))
        assert env.recoverable is True


# ---------------------------------------------------------------------------
# ToolExecutor envelope accumulation
# ---------------------------------------------------------------------------


class TestToolExecutorEnvelopes:
    @pytest.mark.asyncio
    async def test_envelope_recorded_on_successful_dispatch(self):
        executor = _make_executor()

        async def handler(args):
            return "ok result"

        executor.register_dynamic_tool(
            "mytool",
            {
                "name": "mytool",
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
            handler,
        )

        calls = [_make_tool_call("mytool")]
        results = await executor.dispatch(calls)
        assert len(executor.envelopes) == 1
        env = executor.envelopes[0]
        assert env.tool_name == "mytool"
        assert env.is_error is False
        assert env.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_envelope_recorded_on_error(self):
        executor = _make_executor()

        async def failing_handler(args):
            raise ValueError("tool exploded")

        executor.register_dynamic_tool(
            "badtool",
            {
                "name": "badtool",
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
            failing_handler,
        )

        calls = [_make_tool_call("badtool")]
        results = await executor.dispatch(calls)
        assert len(executor.envelopes) == 1
        env = executor.envelopes[0]
        assert env.is_error is True
        assert env.error_class == "ValueError"

    @pytest.mark.asyncio
    async def test_multiple_calls_accumulate_envelopes(self):
        executor = _make_executor()

        async def h1(args):
            return "result1"

        async def h2(args):
            return "result2"

        for name, h in [("t1", h1), ("t2", h2)]:
            executor.register_dynamic_tool(
                name,
                {
                    "name": name,
                    "description": "x",
                    "parameters": {"type": "object", "properties": {}},
                },
                h,
            )

        await executor.dispatch([_make_tool_call("t1", call_id="c1")])
        await executor.dispatch([_make_tool_call("t2", call_id="c2")])
        assert len(executor.envelopes) == 2

    @pytest.mark.asyncio
    async def test_success_rate_all_success(self):
        executor = _make_executor()

        async def h(args):
            return "ok"

        executor.register_dynamic_tool(
            "t",
            {
                "name": "t",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
            h,
        )
        await executor.dispatch([_make_tool_call("t")])
        await executor.dispatch([_make_tool_call("t", call_id="c2")])
        assert executor.success_rate() == 1.0

    @pytest.mark.asyncio
    async def test_success_rate_none_when_no_calls(self):
        executor = _make_executor()
        assert executor.success_rate() is None

    @pytest.mark.asyncio
    async def test_repeated_call_signatures(self):
        executor = _make_executor()

        async def h(args):
            return "ok"

        executor.register_dynamic_tool(
            "search",
            {
                "name": "search",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
            h,
        )
        for i in range(3):
            await executor.dispatch([_make_tool_call("search", call_id=f"c{i}")])
        repeated = executor.repeated_call_signatures()
        assert repeated.get("search", 0) == 3

    @pytest.mark.asyncio
    async def test_cleanup_clears_envelopes(self):
        executor = _make_executor()

        async def h(args):
            return "ok"

        executor.register_dynamic_tool(
            "t",
            {
                "name": "t",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
            h,
        )
        await executor.dispatch([_make_tool_call("t")])
        assert len(executor.envelopes) == 1
        await executor.cleanup()
        assert len(executor.envelopes) == 0

    @pytest.mark.asyncio
    async def test_timeout_produces_error_envelope(self):
        import asyncio

        executor = ToolExecutor(call_timeout=0.001, sanitize_errors=False)

        async def slow_handler(args):
            await asyncio.sleep(10)
            return "never"

        executor.register_dynamic_tool(
            "slow",
            {
                "name": "slow",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
            slow_handler,
        )
        results = await executor.dispatch([_make_tool_call("slow")])
        assert len(executor.envelopes) == 1
        env = executor.envelopes[0]
        assert env.is_error is True
        assert "timed out" in results[0]["content"]
