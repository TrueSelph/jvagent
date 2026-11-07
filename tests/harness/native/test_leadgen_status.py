"""Qualify the existing visitor-bound lead status against real graph ownership."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.leadgen.leadgen_action import LeadGenAction
from jvagent.action.leadgen.store import LeadRecord
from jvagent.harness.native import build_native_agent
from jvagent.memory.conversation import Conversation
from jvagent.memory.user import User
from jvagent.tooling.context import bind_dispatch_context


@pytest.mark.asyncio
async def test_native_status_uses_graph_caller_and_ignores_model_identity(
    tmp_path: Path,
) -> None:
    user = await User.create(memory_id="memory", user_id="caller")
    other = await User.create(memory_id="memory", user_id="other")
    own = await LeadRecord.get_or_create_for_user(user, required_fields=["full_name"])
    await own.set_yaml({"full_name": "Casey"})
    other_record = await LeadRecord.get_or_create_for_user(
        other, required_fields=["email"]
    )
    await other_record.set_yaml({"email": "private fixture"})
    conversation = await Conversation.create(user_id=user.user_id, session_id="session")
    await user.connect(conversation, direction="out")
    interaction = await conversation.add_interaction(
        utterance="Lead status", session_id="session"
    )
    v = SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id=user.user_id,
        session_id="session",
        channel="default",
        interaction=interaction,
    )
    action = LeadGenAction(
        default_fields={"full_name": {"required": True}, "email": {"required": True}}
    )
    directory = tmp_path / "status"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: status\ndescription: Own status\nallowed-tools: [leadgen__status]\n---\nRead status.\n"
    )
    observed: list[dict[str, Any]] = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(
                parts=[ToolCallPart("leadgen__status", {"user_id": "other"})]
            )
        observed.append(json.loads(returns[-1].content))
        return ModelResponse(parts=[TextPart("done")])

    async def access(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=await action.get_tools(),
        access_check=access,
        instructions="Read own status.",
    )
    with bind_dispatch_context(v):
        await agent.run("Status")
    assert observed[0]["missing_fields"] == ["email"]
    assert observed[0]["score"] == 50
    assert (await LeadRecord.get_for_user(user)).get_required_fields() == ["full_name"]


@pytest.mark.asyncio
async def test_native_retrieve_does_not_create_or_rewrite_a_profile() -> None:
    user = await User.create(memory_id="memory", user_id="caller")
    conversation = await Conversation.create(user_id=user.user_id, session_id="session")
    await user.connect(conversation, direction="out")
    interaction = await conversation.add_interaction(
        utterance="Lead profile", session_id="session"
    )
    visitor = SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id=user.user_id,
        session_id="session",
        channel="default",
        interaction=interaction,
    )
    action = LeadGenAction(default_fields={"email": {"required": True}})
    tools = {tool.name: tool for tool in await action.get_native_tools()}
    assert set(tools) == {"leadgen__status", "leadgen__retrieve"}
    retrieve = tools["leadgen__retrieve"]
    assert retrieve.requires_tool_permission and retrieve.effect_class == "read"
    assert retrieve.parameters_schema["additionalProperties"] is False
    result = json.loads(await retrieve.execute(visitor=visitor))
    assert result["status"] == "empty_profile"
    assert result["missing_fields"] == ["email"]
    assert await LeadRecord.get_for_user(user) is None

    record = await LeadRecord.get_or_create_for_user(user, required_fields=["name"])
    await record.set_yaml({"name": "Casey", "_private": "hidden"})
    result = json.loads(await retrieve.execute(visitor=visitor))
    assert result["fields"] == {"name": "Casey"}
    assert result["missing_fields"] == ["email"]
    assert (await LeadRecord.get_for_user(user)).get_required_fields() == ["name"]
