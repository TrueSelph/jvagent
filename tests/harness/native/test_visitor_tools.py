"""Visitor compatibility must never place caller authority in model arguments."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.tooling.context import bind_dispatch_context, get_dispatch_context
from jvagent.tooling.tool import Tool


def visitor(user: str = "caller") -> Any:
    return SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id=user,
        session_id="session-" + user,
        channel="default",
        interaction=SimpleNamespace(
            id="turn-" + user, user_id=user, session_id="session-" + user
        ),
    )


def build(
    tmp_path: Path,
    called: list[str],
    *,
    arguments: dict[str, Any] | None = None,
    authorized: bool = True,
) -> Any:
    directory = tmp_path / "scope"
    directory.mkdir(exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: scope\ndescription: Caller status\nallowed-tools: [status]\n---\nRead status.\n"
    )
    step = 0

    async def check(name: str) -> bool:
        return authorized

    async def status(visitor: Any) -> str:
        import asyncio

        await asyncio.sleep(0)
        called.append(visitor.user_id)
        assert get_dispatch_context().user_id == visitor.user_id
        return visitor.user_id

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            tool = next(t for t in info.function_tools if t.name == "status")
            assert "visitor" not in tool.parameters_json_schema.get("properties", {})
            return ModelResponse(parts=[ToolCallPart("status", arguments or {})])
        return ModelResponse(parts=[TextPart("done")])

    return build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[
            Tool(
                "status",
                "Status",
                parameters_schema={
                    "type": "object",
                    "properties": {"visitor": {"type": "object"}},
                    "required": ["visitor"],
                },
                execute=status,
                effect_class="read",
                binds_visitor=True,
            )
        ],
        access_check=check,
        instructions="Use status.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_host_bound_visitor_injection(tmp_path: Path, authorized: bool) -> None:
    called: list[str] = []
    agent = build(tmp_path, called, authorized=authorized)
    with bind_dispatch_context(visitor()):
        await agent.run("Read status")
    assert called == (["caller"] if authorized else [])
    assert get_dispatch_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "visitor", "interaction"])
async def test_missing_or_changed_host_context_prevents_execution(
    tmp_path: Path, mismatch: str
) -> None:
    called: list[str] = []
    agent = build(tmp_path, called)
    v = visitor()
    with bind_dispatch_context(None if mismatch == "missing" else v):
        if mismatch == "visitor":
            v.user_id = "other"
        if mismatch == "interaction":
            v.interaction.user_id = "other"
        with pytest.raises(ValueError):
            await agent.run("Read status")
    assert not called


@pytest.mark.asyncio
async def test_model_cannot_supply_the_host_visitor(tmp_path: Path) -> None:
    called: list[str] = []
    agent = build(tmp_path, called, arguments={"visitor": {"user_id": "other"}})
    with bind_dispatch_context(visitor()):
        with pytest.raises(ValueError, match="authority"):
            await agent.run("Forge caller")
    assert not called


@pytest.mark.asyncio
async def test_parallel_callers_keep_separate_host_visitors(tmp_path: Path) -> None:
    import asyncio

    called: list[str] = []

    async def run(user: str) -> None:
        agent = build(tmp_path, called)
        with bind_dispatch_context(visitor(user)):
            await agent.run("Read status")

    await asyncio.gather(run("one"), run("two"))
    assert sorted(called) == ["one", "two"]
    assert get_dispatch_context() is None
