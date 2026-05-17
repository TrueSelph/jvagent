"""Tool-error sanitization (AUDIT-interact-cockpit CRIT-05).

Many harness tools (``cockpit/tools/conversation.py``, ``.../memory.py``,
``.../clock.py``) return ``f"Error: {exc}"`` directly when they catch an
exception. The cockpit engine's all-errors short-circuit aggregates these
into ``error_details`` and previously passed them through
``_emit_tool_error_thought`` unsanitized — bypassing the
``sanitize_tool_errors`` config flag.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _build_engine(sanitize: bool, stream_internal_progress: bool = True):
    from jvagent.action.cockpit.engine import CockpitEngine

    bus = SimpleNamespace(publish=AsyncMock())
    interaction = SimpleNamespace(id="int_x")
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            sanitize_tool_errors=sanitize,
            stream_internal_progress=stream_internal_progress,
        ),
        response_bus=bus,
        session_id="sess_x",
        interaction=interaction,
        channel="web",
        stream=False,
        user_id="u_x",
    )
    engine = CockpitEngine.__new__(CockpitEngine)  # bypass full __init__
    engine.ctx = ctx
    return engine, bus


@pytest.mark.asyncio
async def test_sanitize_strips_raw_exception_content():
    engine, bus = _build_engine(sanitize=True)

    raw = (
        "- conversation_search: Error: division by zero at /etc/secret/path\n"
        "- memory_set: Error: connection refused 10.0.0.5:5432"
    )
    await engine._emit_tool_error_thought(raw)

    bus.publish.assert_awaited_once()
    streamed = bus.publish.call_args.kwargs["content"]
    # Tool names preserved, raw exception bodies replaced.
    assert "conversation_search" in streamed
    assert "memory_set" in streamed
    assert "division by zero" not in streamed
    assert "/etc/secret/path" not in streamed
    assert "10.0.0.5" not in streamed
    assert "error" in streamed.lower()


@pytest.mark.asyncio
async def test_no_sanitize_keeps_raw_content():
    engine, bus = _build_engine(sanitize=False)
    raw = "- conversation_search: Error: division by zero"
    await engine._emit_tool_error_thought(raw)
    bus.publish.assert_awaited_once()
    streamed = bus.publish.call_args.kwargs["content"]
    assert "division by zero" in streamed


@pytest.mark.asyncio
async def test_disabled_stream_does_not_publish_but_still_logs(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="jvagent.action.cockpit.engine")
    engine, bus = _build_engine(sanitize=True, stream_internal_progress=False)
    raw = "- toolA: Error: secret thing"
    await engine._emit_tool_error_thought(raw)
    bus.publish.assert_not_awaited()
    # Still logged for ops via standard logger.
    assert any(
        "Cockpit tool batch failed" in record.message for record in caplog.records
    )
