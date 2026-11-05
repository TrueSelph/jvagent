"""Native domain mutations use public SDK effects and durable JV sessions."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.interview import InterviewAction
from jvagent.action.interview.native import qualify_spec
from jvagent.action.interview.session import load_session
from jvagent.action.interview.spec import FieldDef, InterviewSpec
from jvagent.harness.conversation_tools import (
    CONVERSATION_EFFECT,
    ConversationMutationTool,
)
from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.task_store import TaskStore
from jvagent.tooling.context import bind_dispatch_context
from jvagent.tooling.tool import Tool


async def host() -> Any:
    conversation = Conversation(user_id="caller", session_id="session")
    await conversation.save()
    return SimpleNamespace(
        _agent=SimpleNamespace(
            id="agent", get_action_by_type=AsyncMock(return_value=None)
        ),
        user_id="caller",
        session_id="session",
        channel="default",
        conversation=conversation,
        tasks=TaskStore(conversation),
    )


async def turn(
    directory: Path,
    visitor: Any,
    tools: list[Tool],
    commands: list[tuple[str, dict[str, Any]]],
    bases: Any = None,
) -> tuple[Any, Any]:
    interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id="caller",
        session_id="session",
        utterance="test",
    )
    await interaction.save()
    visitor.interaction = interaction
    store = InteractionStepStore(interaction)
    steps = iter(commands)

    def model(messages: Any, info: Any) -> ModelResponse:
        command = next(steps, None)
        if command:
            return ModelResponse(parts=[ToolCallPart(command[0], command[1])])
        return ModelResponse(parts=[TextPart("done")])

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(model),
        skill_directory=directory,
        tools=tools,
        access_check=allow,
        instructions="Use explicit tools.",
        step_store=store,
        available_actions=["InterviewAction"],
        action_skill_bases=bases,
    )
    with bind_dispatch_context(visitor):
        result = await agent.run(
            "test", run_id=interaction.id, conversation_id="session"
        )
    return result, store


def declaration(tmp_path: Path, fields: dict[str, Any]) -> Path:
    directory = tmp_path / "profile"
    directory.mkdir(exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "profile",
                "description": "Profile",
                "extends": "action:jvagent/interview",
                "interview": {
                    "fields": [
                        {"key": "email", "prompt": "Email?", "validator": "email"}
                    ]
                },
                **fields,
            }
        )
        + "---\nCollect an email.\n"
    )
    return directory


@pytest.mark.asyncio
async def test_native_interview_validation_review_and_later_confirmation(
    tmp_path: Path,
) -> None:
    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    result, store = await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"email": "invalid"}}),
            ("interview__set_fields", {"fields": {"email": "member@example.com"}}),
            ("interview__review", {}),
            ("interview__complete", {}),
        ],
        bases,
    )
    outputs = [
        json.loads(p.content)
        for m in result.all_messages()
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_name.startswith("interview__")
    ]
    assert outputs[0]["fields"] == {}
    assert outputs[1]["ok"] is False
    assert outputs[2]["ok"] is True
    assert outputs[-1]["ok"] is False
    assert "confirmation" in outputs[-1]["error"].lower()
    assert load_session(visitor.conversation).status.value == "review"
    effects = visitor.interaction.native_steps["effects"]
    assert len(effects) == 5
    assert all(
        e["status"] == "completed" and e["effect_summary"] == CONVERSATION_EFFECT
        for e in effects
    )
    _, store = await turn(
        directory, visitor, tools, [("interview__complete", {})], bases
    )
    assert load_session(visitor.conversation) is None
    assert visitor.tasks.list()[0].status == "completed"
    assert "state" not in visitor.tasks.list()[0].data
    assert not await store.list_unresolved_tool_effects(run_id=visitor.interaction.id)


@pytest.mark.asyncio
async def test_native_reset_and_cancel_preserve_platform_state(tmp_path: Path) -> None:
    from jvagent.core.host_context import HOST_CONTEXT_REPLAY_KEY

    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    visitor.conversation.context = {
        HOST_CONTEXT_REPLAY_KEY: {"consumed": {"expires_at": 9999999999}}
    }
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"email": "member@example.com"}}),
            ("interview__reset", {}),
        ],
        bases,
    )
    assert load_session(visitor.conversation).fields == {}
    await turn(directory, visitor, tools, [("interview__cancel", {})], bases)
    assert load_session(visitor.conversation) is None
    assert HOST_CONTEXT_REPLAY_KEY in visitor.conversation.context
    assert visitor.tasks.list()[-1].status == "cancelled"


@pytest.mark.parametrize(
    "change", ["handler", "validator", "preprocessor", "branch", "task", "parameters"]
)
def test_native_unreviewed_extensions_reject(change: str) -> None:
    spec = InterviewSpec(
        name="profile", fields=[FieldDef(key="email", prompt="Email?")]
    )
    if change == "handler":
        spec.handlers.complete = "send_email"
    if change == "validator":
        spec.fields[0].validator = "external_check"
    if change == "preprocessor":
        spec.fields[0].pre_processor = ["side_effect"]
    if change == "branch":
        spec.fields[0].else_field = "another"
    if change == "task":
        spec.nudge_after = "1h"
    if change == "parameters":
        spec.parameters = [{"scope": "response", "response": "legacy"}]
    with pytest.raises(ValueError):
        qualify_spec(spec)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", [False, True])
async def test_failed_run_after_mutation_never_falls_back_to_prewrite_input(
    tmp_path: Path,
    provider_failure: bool,
) -> None:
    directory = tmp_path / "mutate"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mutate\ndescription: Mutate\nallowed-tools: [write]\n---\nWrite.\n"
    )
    visitor = await host()
    interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id="caller",
        session_id="session",
        utterance="test",
    )
    await interaction.save()
    visitor.interaction = interaction
    store = InteractionStepStore(interaction)

    async def write(visitor: Any) -> str:
        visitor.conversation.context["mutated"] = True
        await visitor.conversation.save()
        if not provider_failure:
            raise RuntimeError("failure after mutation")
        return "stored"

    async def allow(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        if visitor.conversation.context.get("mutated"):
            raise RuntimeError("provider failure after mutation")
        return ModelResponse(parts=[ToolCallPart("write", {})])

    model = FunctionModel(respond)
    tool = ConversationMutationTool(
        "write", "Write", execute=write, effect_class="write", binds_visitor=True
    )
    agent = build_native_agent(
        model,
        skill_directory=directory,
        tools=[tool],
        access_check=allow,
        instructions="Write",
        step_store=store,
    )
    with (
        bind_dispatch_context(visitor),
        pytest.raises(RuntimeError, match="after mutation"),
    ):
        await agent.run("test", run_id="write-failed", conversation_id="session")
    interaction.native_run = {"status": "failed", "run_id": "write-failed"}
    assert interaction.native_steps["effects"][0]["status"] == (
        "completed" if provider_failure else "failed"
    )
    assert (
        interaction.native_steps["effects"][0]["effect_summary"] == CONVERSATION_EFFECT
    )
    with pytest.raises(NativeHistoryError, match="mutation requires reconciliation"):
        await history_for_new_turn(
            interaction,
            conversation_id=visitor.conversation.id,
            user_id="caller",
            session_id="session",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["save", "flush", "scope"])
async def test_mutation_acknowledgement_and_scope_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from unittest.mock import AsyncMock

    import jvagent.harness.conversation_tools as module

    directory = tmp_path / "mutate"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mutate\ndescription: Mutate\nallowed-tools: [write]\n---\nWrite.\n"
    )
    visitor = await host()

    async def write(visitor: Any) -> str:
        visitor.conversation.context["changed"] = True
        if failure == "scope":
            visitor.conversation.user_id = "another"
        return "stored"

    if failure == "save":
        monkeypatch.setattr(
            type(visitor.conversation),
            "save",
            AsyncMock(side_effect=RuntimeError("save failed")),
        )
    if failure == "flush":
        monkeypatch.setattr(
            module,
            "flush_deferred_entities",
            AsyncMock(side_effect=RuntimeError("flush failed")),
        )
    tool = ConversationMutationTool(
        "write", "Write", execute=write, effect_class="write", binds_visitor=True
    )
    # Saving the Interaction must stay functional when the Conversation save fails.
    with pytest.raises((RuntimeError, ValueError)):
        await turn(directory, visitor, [tool], [("write", {})])
    assert visitor.interaction.native_steps["effects"][0]["status"] != "completed"


def test_plain_write_is_not_a_qualified_conversation_mutation(tmp_path: Path) -> None:
    directory = tmp_path / "mutate"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mutate\ndescription: Mutate\nallowed-tools: [write]\n---\nWrite.\n"
    )

    async def allow(name: str) -> bool:
        return True

    for tool in [
        Tool("write", "Write", effect_class="write"),
        ConversationMutationTool(
            "write", "Write", effect_class="write", binds_visitor=True
        ),
    ]:
        with pytest.raises(ValueError):
            build_native_agent(
                FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("never")])),
                skill_directory=directory,
                tools=[tool],
                access_check=allow,
                instructions="Host",
            )


@pytest.mark.asyncio
async def test_cancelled_mutation_keeps_an_unresolved_effect(tmp_path: Path) -> None:
    import asyncio

    from jvagent.harness.native import run_native_agent

    directory = tmp_path / "mutate"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mutate\ndescription: Mutate\nallowed-tools: [write]\n---\nWrite.\n"
    )
    visitor = await host()
    interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id="caller",
        session_id="session",
        utterance="test",
    )
    await interaction.save()
    visitor.interaction = interaction
    entered = asyncio.Event()

    async def write(visitor: Any) -> str:
        visitor.conversation.context["changed"] = True
        await visitor.conversation.save()
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def allow(name: str) -> bool:
        return True

    store = InteractionStepStore(interaction)
    agent = build_native_agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[ToolCallPart("write", {})])
        ),
        skill_directory=directory,
        tools=[
            ConversationMutationTool(
                "write",
                "Write",
                execute=write,
                effect_class="write",
                binds_visitor=True,
            )
        ],
        access_check=allow,
        instructions="Write",
        step_store=store,
    )
    with bind_dispatch_context(visitor):
        task = asyncio.create_task(
            run_native_agent(
                agent, "test", run_id="cancelled-write", conversation_id="session"
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert visitor.conversation.context["changed"] is True
    effect = await store.get_tool_effect(
        run_id="cancelled-write",
        tool_call_id=interaction.native_steps["effects"][0]["tool_call_id"],
    )
    assert effect.status == "started" and effect.effect_summary == CONVERSATION_EFFECT
    assert await store.list_unresolved_tool_effects(run_id="cancelled-write")


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", ["environment", "action", "lookup-error"])
async def test_native_task_callback_effects_are_not_silently_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    if configured == "environment":
        monkeypatch.setenv(
            "JVAGENT_TASK_COMPLETED_WEBHOOK_URL", "https://example.invalid/hook"
        )
    if configured == "action":
        visitor._agent.get_action_by_type.return_value = SimpleNamespace(
            task_created_webhook_url="https://example.invalid/hook"
        )
    if configured == "lookup-error":
        visitor._agent.get_action_by_type.side_effect = RuntimeError("lookup failed")
    with pytest.raises((ValueError, RuntimeError)):
        await turn(
            directory,
            visitor,
            await action.get_native_tools(),
            [("interview__start", {"interview_type": "profile"})],
            await action.get_native_skill_bases(),
        )
    assert load_session(visitor.conversation) is None
    assert not visitor.tasks.list()


@pytest.mark.asyncio
async def test_swallowed_legacy_task_close_failure_cannot_report_native_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"email": "member@example.com"}}),
            ("interview__review", {}),
        ],
        bases,
    )
    original = visitor.tasks._persist_task

    async def fail_terminal(task: Any) -> None:
        if task.status == "completed":
            raise RuntimeError("terminal persistence failed")
        await original(task)

    monkeypatch.setattr(visitor.tasks, "_persist_task", fail_terminal)
    with pytest.raises(RuntimeError, match="terminal state"):
        await turn(directory, visitor, tools, [("interview__complete", {})], bases)
    assert visitor.tasks.list()[0].status == "active"
    assert visitor.interaction.native_steps["effects"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_denied_conversation_mutation_is_terminal_before_execution(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "mutate"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mutate\ndescription: Mutate\nallowed-tools: [write]\n---\nWrite.\n"
    )
    visitor = await host()
    interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id="caller",
        session_id="session",
        utterance="test",
    )
    await interaction.save()
    visitor.interaction = interaction
    called = []

    async def write(visitor: Any) -> str:
        called.append(True)
        return "wrong"

    async def deny(name: str) -> bool:
        return False

    store = InteractionStepStore(interaction)
    agent = build_native_agent(
        FunctionModel(lambda m, i: ModelResponse(parts=[ToolCallPart("write", {})])),
        skill_directory=directory,
        tools=[
            ConversationMutationTool(
                "write",
                "Write",
                execute=write,
                effect_class="write",
                binds_visitor=True,
            )
        ],
        access_check=deny,
        instructions="Write",
        step_store=store,
    )
    with (
        bind_dispatch_context(visitor),
        pytest.raises(PermissionError, match="access denied"),
    ):
        await agent.run("test", run_id="denied-write", conversation_id="session")
    assert called == []
    assert interaction.native_steps["effects"][0]["status"] == "failed"
    assert (
        interaction.native_steps["effects"][0].get("effect_summary")
        != CONVERSATION_EFFECT
    )


@pytest.mark.asyncio
async def test_other_registered_specs_cannot_bypass_their_own_skill_policy(
    tmp_path: Path,
) -> None:
    from jvagent.action.interview.spec import load_interview_spec_from_skill

    for policy in [
        {"allowed-groups": ["staff"]},
        {"task-lock": True},
        {"allowed-channels": ["sms"]},
        {"disabled-tools": ["interview__start"]},
    ]:
        directory = declaration(tmp_path, policy)
        spec = load_interview_spec_from_skill(directory)
        assert spec is not None
        with pytest.raises(ValueError):
            qualify_spec(spec, channel="default", tool_name="interview__start")


@pytest.mark.asyncio
async def test_manual_review_cannot_authorize_corrected_values(tmp_path: Path) -> None:
    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"email": "old@example.com"}}),
            ("interview__review", {}),
        ],
        bases,
    )
    result, _ = await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__set_fields", {"fields": {"email": "new@example.com"}}),
            ("interview__complete", {}),
        ],
        bases,
    )
    outputs = [
        json.loads(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "interview__complete"
    ]
    assert outputs[-1]["ok"] is False
    assert outputs[-1]["next_tool"] == "interview__review"
    assert load_session(visitor.conversation).fields["email"] == "new@example.com"
    assert visitor.tasks.list()[0].status == "active"


@pytest.mark.asyncio
async def test_missing_review_snapshot_requires_fresh_review(tmp_path: Path) -> None:
    from jvagent.action.interview.session import InterviewStatus, save_session

    directory = declaration(tmp_path, {})
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"email": "member@example.com"}}),
        ],
        bases,
    )
    session = load_session(visitor.conversation)
    session.status = InterviewStatus.REVIEW
    await save_session(visitor.conversation, session)
    result, _ = await turn(
        directory, visitor, tools, [("interview__complete", {})], bases
    )
    output = next(
        json.loads(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "interview__complete"
    )
    assert output["ok"] is False
    assert output["next_tool"] == "interview__review"
    assert load_session(visitor.conversation) is not None
