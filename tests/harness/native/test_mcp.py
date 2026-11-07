"""Native scheduling of reviewed Action tools over an actual MCP stdio session."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.mcp.mcp_action import MCPAction
from jvagent.harness.native import build_native_agent
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction

pytest.importorskip("fastmcp.client")
from jvagent.tooling.context import bind_dispatch_context

NAME = "mcp_records__lookup_record"
SLOW = "mcp_records__slow_lookup"


@pytest.mark.asyncio
@pytest.mark.parametrize("host_limit,service_limit", [(0.2, 30.0), (1.0, 0.2)])
async def test_public_mcp_deadline_cancels_rpc_and_keeps_read_receipts(
    tmp_path: Path, host_limit: float, service_limit: float
) -> None:
    action = MCPAction(
        sandbox_mode=False,
        native_read_tools=[NAME, SLOW],
        mcp_call_timeout=service_limit,
        servers=[
            {
                "name": "records",
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("mcp_fixture.py"))],
                "transport": "stdio",
                "env": {
                    "JV_MCP_FIXTURE_AUDIT": str(tmp_path / "calls.txt"),
                    "JV_MCP_FIXTURE_PID": str(tmp_path / "server.pid"),
                },
            }
        ],
    )
    directory = tmp_path / "records"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: records\ndescription: Read records\nallowed-tools: [{NAME}, {SLOW}]\n---\nRead.\n"
    )
    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        await action._build_server_entries()
    interaction = Interaction(utterance="slow then fast")
    await interaction.save()
    store = InteractionStepStore(interaction)
    calls = 0

    async def access(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart(SLOW, {}, tool_call_id="slow")])
        if calls == 2:
            failures = [
                p
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_call_id == "slow"
            ]
            assert failures and failures[-1].outcome != "success"
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        NAME, {"reference": "REHEARSAL-47"}, tool_call_id="fast"
                    )
                ]
            )
        assert "Cedar" in str(messages)
        return ModelResponse(parts=[TextPart("verified")])

    try:
        toolsets = await action.get_native_toolsets(tool_timeout_seconds=host_limit)
        assert toolsets[0].tool_timeout_seconds == min(host_limit, service_limit)
        agent = build_native_agent(
            FunctionModel(respond),
            skill_directory=directory,
            tools=[],
            toolsets=toolsets,
            access_check=access,
            instructions="Read.",
            step_store=store,
            tool_timeout_seconds=host_limit,
        )
        with bind_dispatch_context(host("deadline")):
            result = await agent.run(
                "read", run_id="rpc-run", conversation_id="rpc-session"
            )
        assert result.output == "verified" and calls == 3
        assert (
            await store.get_tool_effect(run_id="rpc-run", tool_call_id="slow")
        ).status == "failed"
        assert (
            await store.get_tool_effect(run_id="rpc-run", tool_call_id="fast")
        ).status == "completed"
        assert not await store.list_unresolved_tool_effects(run_id="rpc-run")
        audit = (tmp_path / "calls.txt").read_text().splitlines()
        assert sorted(audit) == sorted(["SLOW-start", "SLOW-stop", "REHEARSAL-47"])
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "server.pid").read_text()), 0)
    finally:
        await action.on_disable()


def host(user: str) -> Any:
    return SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id=user,
        session_id="session-" + user,
        interaction=SimpleNamespace(
            id="turn-" + user, user_id=user, session_id="session-" + user
        ),
        channel="default",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_mode", [False, True])
@pytest.mark.parametrize(
    "reference,permitted",
    [("REHEARSAL-47", True), ("absent", True), ("REHEARSAL-47", False)],
)
async def test_native_mcp_transport_and_policy(
    tmp_path: Path, reference: str, permitted: bool, catalog_mode: bool
) -> None:
    action = MCPAction(
        sandbox_mode=False,
        native_read_tools=[NAME],
        servers=[
            {
                "name": "records",
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("mcp_fixture.py"))],
                "transport": "stdio",
                "env": {
                    "JV_MCP_FIXTURE_AUDIT": str(tmp_path / "calls.txt"),
                    "JV_MCP_FIXTURE_PID": str(tmp_path / "server.pid"),
                },
            }
        ],
    )
    directory = tmp_path / "records"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: records\ndescription: Read records\nallowed-tools: [{NAME}]\n---\nUse the record tool.\n"
    )
    with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
        await action._build_server_entries()
    try:
        toolsets = await action.get_native_toolsets()
        checked: list[str] = []

        async def access(label: str) -> bool:
            checked.append(label)
            return permitted

        count = 0

        def respond(messages: Any, info: Any) -> ModelResponse:
            nonlocal count
            count += 1
            assert "mcp_records__misleading_write" not in {
                t.name for t in info.function_tools
            }
            if catalog_mode and count == 1:
                assert NAME not in {t.name for t in info.function_tools}
                return ModelResponse(
                    parts=[ToolCallPart("load_capability", {"id": "records"})]
                )
            if count == (2 if catalog_mode else 1):
                return ModelResponse(
                    parts=[ToolCallPart(NAME, {"reference": reference})]
                )
            retries = [
                p
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.outcome == "retried"
            ]
            if not permitted:
                assert retries and "denied" in str(retries[-1].content)
            elif reference == "absent":
                assert retries and "Error executing tool" in str(retries[-1].content)
            else:
                assert not retries
                assert "Cedar" in str(messages) and "14:30" in str(messages)
            return ModelResponse(parts=[TextPart("verified")])

        interaction = Interaction(utterance="Read the record.")
        await interaction.save()
        store = InteractionStepStore(interaction)
        agent = build_native_agent(
            FunctionModel(respond),
            skill_directory=None if catalog_mode else directory,
            skill_directories=[directory] if catalog_mode else [],
            tools=[],
            step_store=store,
            toolsets=toolsets,
            access_check=access,
            instructions="Follow the skill.",
        )
        with bind_dispatch_context(host("alice")):
            result = await agent.run("Read the record.")
        assert result.output == "verified"
        assert checked == [NAME]
        all_effects = interaction.native_steps["effects"]
        assert len(all_effects) == (2 if catalog_mode else 1)
        if catalog_mode:
            assert all_effects[0]["tool_name"] == "load_capability"
            assert all_effects[0]["status"] == "completed"
        effects = [effect for effect in all_effects if effect["tool_name"] == NAME]
        assert len(effects) == 1
        assert effects[0]["status"] == (
            "completed" if permitted and reference == "REHEARSAL-47" else "failed"
        )
        expected_event = (
            "tool_call_completed"
            if permitted and reference == "REHEARSAL-47"
            else "tool_call_failed"
        )
        assert expected_event in [e["kind"] for e in interaction.native_steps["events"]]
        assert not await store.list_unresolved_tool_effects(run_id=effects[0]["run_id"])
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "server.pid").read_text()), 0)
        audit = tmp_path / "calls.txt"
        assert (audit.read_text().splitlines() if audit.exists() else []) == (
            [reference] if permitted else []
        )
        assert not any(
            task
            for task in asyncio.all_tasks()
            if "MCPClientWrapper" in repr(task.get_coro())
        )
    finally:
        await action.on_disable()
        await asyncio.sleep(0)
    assert (
        action.get_client("records")._session is None
    )  # legacy client was never connected
