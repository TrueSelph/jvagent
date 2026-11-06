"""Official SDK dispatch must retain file read identity and host permissions."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.file_interface import FileInterfaceAction, _core
from jvagent.harness.contracts import HarnessContractError
from jvagent.harness.native import build_native_agent
from jvagent.tooling.context import bind_dispatch_context


def visitor() -> Any:
    return SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id="alice",
        session_id="session",
        channel="default",
        interaction=SimpleNamespace(id="turn", user_id="alice", session_id="session"),
    )


@pytest.mark.asyncio
async def test_native_file_profile_preserves_legacy_surface() -> None:
    action = FileInterfaceAction()
    original = {t.name for t in await action.get_tools()}
    assert "file_interface__write_file" in original
    native = await action.get_native_tools()
    assert [t.name for t in native] == ["file_interface__read_file"]
    assert native[0].binds_visitor and native[0].effect_class == "read"
    assert "visitor" not in native[0].parameters_schema["properties"]
    with pytest.raises(ValueError, match="host context"):
        await native[0].call(visitor=visitor(), path="note.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorized,forged", [(True, False), (False, False), (True, True)]
)
async def test_sdk_file_read_cannot_override_scope_or_host_denial(
    tmp_path: Path,
    monkeypatch: Any,
    authorized: bool,
    forged: bool,
) -> None:
    action = FileInterfaceAction()
    tools = await action.get_native_tools()
    called: list[str] = []

    async def bounded(host: Any, path: str, **kwargs: Any) -> str:
        called.append(host.user_id)
        assert kwargs["max_bytes"] == 262_144
        return "ALICE_RECORD"

    monkeypatch.setattr(_core, "read_bounded_text_file", bounded)
    skill = tmp_path / "file-read"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: file-read\ndescription: Read a caller file\n"
        "allowed-tools: [file_interface__read_file]\n---\nRead the requested file.\n"
    )
    responses = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal responses
        responses += 1
        if responses == 1:
            arguments: dict[str, Any] = {"path": "note.txt"}
            if forged:
                arguments["visitor"] = {"user_id": "bob"}
            return ModelResponse(
                parts=[ToolCallPart("file_interface__read_file", arguments)]
            )
        return ModelResponse(parts=[TextPart("done")])

    async def check(name: str) -> bool:
        return authorized

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=tools,
        access_check=check,
        instructions="Read the requested file.",
    )
    with bind_dispatch_context(visitor()):
        if forged:
            with pytest.raises(HarnessContractError, match="authority field"):
                await agent.run("Read note.txt")
        else:
            await agent.run("Read note.txt")
    assert called == (["alice"] if authorized and not forged else [])


@pytest.mark.asyncio
async def test_changed_visitor_during_read_cannot_publish_content(
    monkeypatch: Any,
) -> None:
    host = visitor()

    async def bounded(*args: Any, **kwargs: Any) -> str:
        host.user_id = "bob"
        return "PRIVATE_CONTENT"

    monkeypatch.setattr(_core, "read_bounded_text_file", bounded)
    tool = (await FileInterfaceAction().get_native_tools())[0]
    with bind_dispatch_context(host), pytest.raises(ValueError, match="scope changed"):
        await tool.call(visitor=host, path="note.txt")


@pytest.mark.asyncio
async def test_provider_exception_does_not_disclose_host_paths(
    monkeypatch: Any,
) -> None:
    async def bounded(*args: Any, **kwargs: Any) -> str:
        raise OSError("/private/other-user/secret.txt")

    monkeypatch.setattr(_core, "read_bounded_text_file", bounded)
    tool = (await FileInterfaceAction().get_native_tools())[0]
    host = visitor()
    with bind_dispatch_context(host):
        result = await tool.call(visitor=host, path="note.txt")
    assert not json.loads(result.content)["ok"]
    assert "/private/other-user" not in result.content
