"""Exact channel projection cannot activate or authorize a sibling skill."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.harness.skill_assets import channel_skill_directories
from jvagent.tooling.tool import Tool
from tests.harness.native.test_skill_catalog import skill


@pytest.mark.parametrize(
    "channel,expected",
    [
        ("default", ["ordinary"]),
        ("whatsapp", ["ordinary", "forms"]),
        ("whatsapp_call", ["ordinary"]),
        ("whatsapp_other", ["ordinary"]),
        ("custom", ["ordinary"]),
    ],
)
def test_exact_allowlist_and_deny_precedence(
    tmp_path: Path, channel: str, expected: list[str]
) -> None:
    ordinary = skill(tmp_path, "ordinary")
    forms = skill(
        tmp_path,
        "forms",
        "allowed-channels: [whatsapp, whatsapp_call]\n"
        "denied-channels: [whatsapp_call]\n",
    )
    assert [
        p.name for p in channel_skill_directories([ordinary, forms], channel=channel)
    ] == expected


@pytest.mark.parametrize("declaration", ["default", "[default, 1]", "[null]"])
def test_malformed_closed_channel_policy_is_not_hidden(
    tmp_path: Path, declaration: str
) -> None:
    ordinary = skill(tmp_path, "ordinary")
    malformed = skill(
        tmp_path,
        "forms",
        "allowed-channels: [whatsapp]\ndenied-channels: " + declaration + "\n",
    )
    with pytest.raises(ValueError, match="denied-channels"):
        channel_skill_directories([ordinary, malformed], channel="default")


def test_duplicate_closed_skill_is_not_hidden(tmp_path: Path) -> None:
    ordinary = skill(tmp_path, "ordinary")
    forms = skill(tmp_path, "forms", "allowed-channels: [whatsapp]\n")
    with pytest.raises(ValueError, match="Duplicate"):
        channel_skill_directories([ordinary, forms, forms], channel="default")


@pytest.mark.asyncio
async def test_closed_skill_and_tool_cannot_be_loaded_or_invoked(
    tmp_path: Path,
) -> None:
    ordinary = skill(tmp_path, "ordinary")
    forms = skill(
        tmp_path,
        "forms",
        "allowed-channels: [whatsapp]\nallowed-tools: [forms_read]\n"
        "requires-actions: [UnavailableOnWeb]\nrequires-tools: [forms_read]\n",
    )
    calls = []
    step = 0

    async def read() -> str:
        calls.append("body")
        return "form"

    async def access(name: str) -> bool:
        calls.append("access")
        return True

    def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        assert "forms instructions" not in str(messages)
        assert "forms_read" not in {t.name for t in info.function_tools}
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "forms"})]
            )
        if step == 2:
            assert not calls
            return ModelResponse(parts=[ToolCallPart("forms_read", {})])
        assert not calls
        return ModelResponse(parts=[TextPart("ordinary conversation continues")])

    agent = build_native_agent(
        FunctionModel(model),
        skill_directories=[ordinary, forms],
        tools=[Tool("forms_read", "Read", execute=read, effect_class="read")],
        access_check=access,
        instructions="Use available capabilities.",
        channel="default",
    )
    assert (await agent.run("open forms")).output == "ordinary conversation continues"
    assert not calls


@pytest.mark.asyncio
async def test_host_script_policy_and_sdk_use_only_channel_eligible_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jvagent.action.native_harness import native_harness_action as module
    from jvagent.harness.script_sandbox import NativeScriptSandbox
    from jvagent.memory.interaction import Interaction
    from tests.harness.native.test_interview import host

    ordinary = skill(tmp_path, "ordinary")
    closed = skill(tmp_path, "closed", "allowed-channels: [whatsapp]\n")
    for directory in (ordinary, closed):
        (directory / "scripts").mkdir()
        (directory / "scripts/run.py").write_text("print(56)\n")
    asset_reader = NativeScriptSandbox(image="python@sha256:" + "0" * 64)
    bound_assets = []

    async def bind(conversation: Any, **scope: Any) -> None:
        bound_assets.append(scope["assets"])

    # No Docker or shell execution is claimed: this checks real host/factory
    # wiring of asset policy while the SDK runs an ordinary model reply.
    sandbox = SimpleNamespace(
        assets=asset_reader.assets,
        bind_policy=bind,
        capabilities=lambda assets: [],
    )
    provider = SimpleNamespace(
        id="scripts",
        get_manifest=lambda: SimpleNamespace(pattern_orchestrator=False),
        get_class_name=lambda: "ScriptProvider",
        get_native_script_sandbox=AsyncMock(return_value=sandbox),
    )
    visitor = await host()
    visitor.interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id=visitor.user_id,
        session_id=visitor.session_id,
        utterance="hello",
    )
    await visitor.interaction.save()
    visitor.utterance = "hello"
    visitor.data = {}
    visitor._agent.get_actions = AsyncMock(return_value=[provider])
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(
        module.NativeHarnessAction,
        "get_agent",
        AsyncMock(return_value=visitor._agent),
    )
    monkeypatch.setattr(
        module.NativeHarnessAction,
        "get_responder",
        AsyncMock(return_value=SimpleNamespace(publish=publish)),
    )

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        yield "hello"

    def factory(model: str, **settings: Any) -> Any:
        from pydantic_ai import Agent

        assert settings["skill_directories"] == [ordinary]
        assert asset_reader.assets(settings["skill_directories"]) == bound_assets[0]
        return Agent(
            FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart("hello")]),
                stream_function=stream,
            ),
        )

    monkeypatch.setattr(module, "build_native_agent", factory)
    action = module.NativeHarnessAction(
        model="test", skill_directories=[str(ordinary), str(closed)]
    )
    await action._execute(visitor)
    assert visitor.interaction.native_run["status"] == "delivered"
    assert len(bound_assets) == 1
    assert set(bound_assets[0]) == {"staged_skills/ordinary/scripts/run.py"}
    assert asset_reader._asset_directories == (ordinary.resolve(),)
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_eligible_skills_refuse_before_model(tmp_path: Path) -> None:
    forms = skill(tmp_path, "forms", "allowed-channels: [whatsapp]\n")
    calls = []

    def model(messages: Any, info: Any) -> ModelResponse:
        calls.append(True)
        return ModelResponse(parts=[TextPart("unexpected")])

    async def access(name: str) -> bool:
        return True

    for config in ({"skill_directory": forms}, {"skill_directories": [forms]}):
        with pytest.raises(ValueError, match="unavailable on this channel"):
            build_native_agent(
                FunctionModel(model),
                tools=[],
                access_check=access,
                instructions="Use forms.",
                channel="default",
                **config,
            )
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["whatsapp", "custom"])
@pytest.mark.parametrize("permitted", [True, False])
async def test_eligible_channel_does_not_grant_host_permission(
    tmp_path: Path, channel: str, permitted: bool
) -> None:
    ordinary = skill(tmp_path, "ordinary")
    forms = skill(
        tmp_path,
        "forms",
        "allowed-channels: [whatsapp, custom]\nallowed-tools: [forms_read]\n",
    )
    invoked = []
    checked = []
    step = 0

    async def read() -> str:
        invoked.append(True)
        return "record"

    async def access(name: str) -> bool:
        checked.append(name)
        return permitted

    def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            assert "forms_read" not in {t.name for t in info.function_tools}
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "forms"})]
            )
        if step == 2:
            assert "forms_read" in {t.name for t in info.function_tools}
            return ModelResponse(parts=[ToolCallPart("forms_read", {})])
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(model),
        skill_directories=[ordinary, forms],
        tools=[Tool("forms_read", "Read", execute=read, effect_class="read")],
        access_check=access,
        instructions="Use available capabilities.",
        channel=channel,
    )
    assert (await agent.run("read forms")).output == "done"
    assert checked == ["forms_read"]
    assert invoked == ([True] if permitted else [])
