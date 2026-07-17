"""ToolExecutionEngine: dispatch success, unknown-tool, timeout, error
sanitization, JSON-string args, and the dispatch-context binding that
context-aware tools (e.g. per-user MCP) rely on."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_executor import (
    ToolExecutionEngine,
    bind_dispatch_context,
    get_dispatch_context,
    get_tool_visitor,
)
from jvagent.tooling.tool_registry import ToolRegistry
from jvagent.tooling.tool_result import ToolResult


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _call(name: str, args=None, cid: str = "c1") -> dict:
    return {"id": cid, "function": {"name": name, "arguments": args or {}}}


async def test_dispatch_success_returns_content():
    async def _echo(text: str = "") -> str:
        return f"echo:{text}"

    eng = ToolExecutionEngine(
        _registry(Tool(name="echo", description="d", execute=_echo))
    )
    out = await eng.dispatch([_call("echo", {"text": "hi"})])
    assert len(out) == 1
    assert out[0].content == "echo:hi"
    assert out[0].is_error is False
    assert eng.envelopes and eng.envelopes[0].tool_name == "echo"


async def test_unknown_tool_is_error():
    eng = ToolExecutionEngine(_registry())
    out = await eng.dispatch([_call("nope")])
    assert out[0].is_error is True
    assert "not available" in out[0].content


async def test_timeout_returns_error():
    async def _slow(**_) -> str:
        await asyncio.sleep(1.0)
        return "done"

    eng = ToolExecutionEngine(
        _registry(Tool(name="slow", description="d", execute=_slow)),
        call_timeout=0.01,
    )
    out = await eng.dispatch([_call("slow")])
    assert out[0].is_error is True
    assert "timed out" in out[0].content


async def test_error_sanitized_by_default():
    async def _boom(**_):
        raise RuntimeError("secret token abc123 in body")

    reg = _registry(Tool(name="boom", description="d", execute=_boom))
    eng = ToolExecutionEngine(reg, sanitize_errors=True)
    out = await eng.dispatch([_call("boom")])
    assert out[0].is_error is True
    assert "abc123" not in out[0].content  # raw exception text not leaked
    assert "boom" in out[0].content

    eng2 = ToolExecutionEngine(reg, sanitize_errors=False)
    out2 = await eng2.dispatch([_call("boom")])
    assert "abc123" in out2[0].content  # unsanitized includes detail


async def test_json_string_arguments_are_parsed():
    seen = {}

    async def _grab(a: int = 0, b: str = "") -> str:
        seen["a"], seen["b"] = a, b
        return "ok"

    eng = ToolExecutionEngine(
        _registry(Tool(name="grab", description="d", execute=_grab))
    )
    await eng.dispatch([_call("grab", '{"a": 5, "b": "x"}')])
    assert seen == {"a": 5, "b": "x"}


async def test_dispatch_context_exposed_to_tool():
    captured = {}

    async def _ctx(**_) -> str:
        ctx = get_dispatch_context()
        captured["agent_id"] = ctx.agent_id if ctx else None
        captured["user_id"] = ctx.user_id if ctx else None
        captured["visitor"] = get_tool_visitor()
        return "ok"

    visitor = SimpleNamespace(
        _agent=SimpleNamespace(id="n.Agent.123"),
        user_id="u.User.9",
        session_id="sess",
        interaction=SimpleNamespace(id="int.1"),
        channel="web",
    )
    eng = ToolExecutionEngine(
        _registry(Tool(name="ctx", description="d", execute=_ctx)),
        visitor=visitor,
    )
    await eng.dispatch([_call("ctx")])
    assert captured["agent_id"] == "n.Agent.123"
    assert captured["user_id"] == "u.User.9"
    assert captured["visitor"] is visitor
    # ContextVar is reset after dispatch — no leak to the caller.
    assert get_dispatch_context() is None
    assert get_tool_visitor() is None


async def test_bind_dispatch_context_sets_and_resets():
    visitor = SimpleNamespace(
        _agent=SimpleNamespace(id="a1"),
        user_id="u1",
        session_id=None,
        interaction=None,
        channel="web",
    )
    assert get_dispatch_context() is None
    with bind_dispatch_context(visitor):
        ctx = get_dispatch_context()
        assert ctx is not None and ctx.agent_id == "a1" and ctx.user_id == "u1"
        assert get_tool_visitor() is visitor
    assert get_dispatch_context() is None
    assert get_tool_visitor() is None


async def test_non_toolresult_return_is_wrapped():
    async def _dict(**_):
        return {"k": "v"}

    eng = ToolExecutionEngine(_registry(Tool(name="d", description="d", execute=_dict)))
    out = await eng.dispatch([_call("d")])
    assert out[0].is_error is False
    assert '"k": "v"' in out[0].content


async def test_explicit_toolresult_passthrough():
    async def _tr(**_):
        return ToolResult(content="custom", is_error=True)

    eng = ToolExecutionEngine(_registry(Tool(name="tr", description="d", execute=_tr)))
    out = await eng.dispatch([_call("tr")])
    assert out[0].content == "custom" and out[0].is_error is True
