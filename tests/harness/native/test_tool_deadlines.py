"""SDK deadlines must not authorize a second attempt at an uncertain write."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import ModelRetry, ToolFailed, ToolRetryError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.mutation_tools import MutationOutcomeUnknown, NativeMutationTool
from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction
from jvagent.tooling.context import bind_dispatch_context
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_decorator import collect_tools
from jvagent.tooling.tool_decorator import tool as publish_tool
from tests.harness.native.test_interview import host


class ProbeWrite(NativeMutationTool):
    effect_summary = "jv:mutation:deadline-probe:v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("retries", [0, 3])
@pytest.mark.parametrize("deadline_source", ["action", "host"])
@pytest.mark.parametrize(
    "fault", ["timeout", "failed-control", "retry-control", "wrapped-retry"]
)
async def test_sdk_timeout_after_write_stops_retry_and_queued_mutation(
    tmp_path: Path,
    batch: bool,
    retries: int,
    fault: str,
    deadline_source: str,
) -> None:
    skill = tmp_path / "probe"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: probe\ndescription: Probe\nallowed-tools: [probe_write]\n---\nUse the probe.\n"
    )
    visitor = await host()
    visitor.interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id=visitor.user_id,
        session_id=visitor.session_id,
    )
    await visitor.interaction.save()
    store = InteractionStepStore(visitor.interaction)
    writes: list[str] = []
    cancelled = asyncio.Event()

    async def write(visitor: Any) -> str:
        effect = visitor.interaction.native_steps["effects"][-1]
        assert effect["effect_summary"] == ProbeWrite.effect_summary
        writes.append("write")
        try:
            if fault == "failed-control":
                raise ToolFailed("An SDK failure after a write is not rollback")
            if fault == "retry-control":
                raise ModelRetry("A requested retry after a write is not rollback")
            if fault == "wrapped-retry":
                raise ToolRetryError(
                    ToolReturnPart(
                        tool_name="probe_write",
                        tool_call_id=effect["tool_call_id"],
                        content="A wrapped retry after a write is not rollback",
                        outcome="retried",
                    )
                )
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return "unreachable"

    tool = ProbeWrite(
        "probe_write",
        "Write the probe",
        execute=write,
        effect_class="write",
        binds_visitor=True,
        timeout_seconds=0.2 if deadline_source == "action" else None,
    )
    requests = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if (batch and requests == 1) or (not batch and requests <= 2):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "probe_write", {}, tool_call_id=f"write-{requests}-{n}"
                    )
                    for n in range(2 if batch else 1)
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=[tool],
        access_check=allow,
        instructions="Use the probe.",
        step_store=store,
        tool_timeout_seconds=0.2 if deadline_source == "host" else None,
    )
    error: Exception | None = None
    with bind_dispatch_context(visitor):
        try:
            await agent.run(
                "write",
                run_id=visitor.interaction.id,
                conversation_id=visitor.session_id,
                retries=retries,
            )
        except Exception as caught:
            error = caught
    assert isinstance(error, MutationOutcomeUnknown), (
        type(error).__name__,
        writes,
        requests,
    )
    assert writes == ["write"] and cancelled.is_set() and requests == 1
    effects = visitor.interaction.native_steps["effects"]
    assert len(effects) == 1
    assert effects[0]["status"] in {"started", "failed"}
    assert effects[0]["effect_summary"] == ProbeWrite.effect_summary
    events = await store.list_events(run_id=visitor.interaction.id)
    assert sum(e.kind == "run_failed" for e in events) == 1
    visitor.interaction.native_run = {
        "run_id": visitor.interaction.id,
        "status": "failed",
    }
    with pytest.raises(NativeHistoryError, match="mutation requires reconciliation"):
        await history_for_new_turn(
            visitor.interaction,
            conversation_id=visitor.conversation.id,
            user_id=visitor.user_id,
            session_id=visitor.session_id,
        )


@pytest.mark.parametrize(
    "value", [0, -1, True, "private-timeout", float("nan"), float("inf"), 10**1000]
)
def test_invalid_deadline_metadata_fails_without_echoing_values(value: Any) -> None:
    with pytest.raises(ValueError) as caught:
        Tool("read", "Read", timeout_seconds=value)
    assert "private-timeout" not in str(caught.value)


def test_mutated_deadline_is_revalidated_at_native_admission(tmp_path: Path) -> None:
    skill = tmp_path / "probe"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: probe\ndescription: Probe\nallowed-tools: [read]\n---\nRead.\n"
    )
    declared = Tool("read", "Read", effect_class="read")
    declared.timeout_seconds = float("nan")

    async def allow(name: str) -> bool:
        return True

    with pytest.raises(ValueError, match="Tool timeout"):
        build_native_agent(
            FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("unused")])),
            skill_directory=skill,
            tools=[declared],
            access_check=allow,
            instructions="Read.",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host_deadline,expected", [(None, 0.2), (1.0, 0.2), (0.1, 0.1)]
)
async def test_decorated_read_deadline_uses_sdk_and_keeps_retry_receipts(
    tmp_path: Path,
    host_deadline: float | None,
    expected: float,
) -> None:
    class Reads:
        calls = 0

        @publish_tool(name="probe_read", effect_class="read", timeout_seconds=0.2)
        async def read(self) -> str:
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()
            return "verified read"

    service = Reads()
    declared = collect_tools(service)
    assert declared[0].timeout_seconds == 0.2
    assert "timeout_seconds" not in declared[0].to_serialized()["function"][
        "parameters"
    ].get("properties", {})
    directory = tmp_path / "reads"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: reads\ndescription: Read\nallowed-tools: [probe_read]\n---\nRead.\n"
    )

    async def save() -> None:
        pass

    from types import SimpleNamespace

    store = InteractionStepStore(SimpleNamespace(native_steps={}, save=save))
    requests = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        definition = next(t for t in info.function_tools if t.name == "probe_read")
        assert definition.timeout == expected
        if requests < 3:
            return ModelResponse(
                parts=[ToolCallPart("probe_read", {}, tool_call_id=f"read-{requests}")]
            )
        return ModelResponse(parts=[TextPart("verified read")])

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=declared,
        access_check=allow,
        instructions="Read.",
        step_store=store,
        tool_timeout_seconds=host_deadline,
    )
    result = await agent.run("read", run_id="read-run", conversation_id="session")
    assert result.output == "verified read" and service.calls == 2
    assert (
        await store.get_tool_effect(run_id="read-run", tool_call_id="read-1")
    ).status == "failed"
    assert (
        await store.get_tool_effect(run_id="read-run", tool_call_id="read-2")
    ).status == "completed"
    assert not await store.list_unresolved_tool_effects(run_id="read-run")


def test_unknown_toolset_cannot_claim_an_unenforced_default_deadline(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    async def allow(name: str) -> bool:
        return True

    with pytest.raises(ValueError, match="deadline adapter"):
        build_native_agent(
            "test",
            tools=[],
            toolsets=[SimpleNamespace()],
            access_check=allow,
            instructions="Read.",
            skill_directory=tmp_path,
            tool_timeout_seconds=0.2,
        )


@pytest.mark.asyncio
async def test_deadline_before_mutation_intent_has_failed_receipt_and_one_later_body(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "writer"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: writer\ndescription: Write\nallowed-tools: [probe_write]\n---\nWrite only when permitted.\n"
    )
    visitor = await host()
    visitor.interaction = Interaction(
        conversation_id=visitor.conversation.id,
        user_id=visitor.user_id,
        session_id=visitor.session_id,
    )
    await visitor.interaction.save()
    store = InteractionStepStore(visitor.interaction)
    checks = 0
    writes: list[str] = []
    requests = 0

    async def authorize(tool: Any) -> bool:
        nonlocal checks
        checks += 1
        if checks == 1:
            await asyncio.Event().wait()
        return True

    async def write(visitor: Any) -> str:
        assert (
            visitor.interaction.native_steps["effects"][-1]["effect_summary"]
            == ProbeWrite.effect_summary
        )
        writes.append("accepted")
        return "accepted"

    async def allow(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests <= 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "probe_write", {}, tool_call_id=f"admission-{requests}"
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("accepted")])

    tool = ProbeWrite(
        "probe_write",
        "Write",
        execute=write,
        effect_class="write",
        binds_visitor=True,
        requires_tool_permission=True,
    )
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[tool],
        access_check=allow,
        tool_access_check=authorize,
        instructions="Write.",
        step_store=store,
        tool_timeout_seconds=0.5,
    )
    with bind_dispatch_context(visitor):
        result = await agent.run(
            "write", run_id=visitor.interaction.id, conversation_id=visitor.session_id
        )
    assert result.output == "accepted" and writes == ["accepted"] and checks == 2
    first = await store.get_tool_effect(
        run_id=visitor.interaction.id, tool_call_id="admission-1"
    )
    assert first.status == "failed" and not (first.effect_summary or "").startswith(
        "jv:mutation:"
    )
    assert not await store.list_unresolved_tool_effects(run_id=visitor.interaction.id)


@pytest.mark.asyncio
async def test_sdk_control_loader_remains_bounded_by_sdk_run_timeout(
    tmp_path: Path,
) -> None:
    from pydantic_ai.capabilities import Hooks

    directory = tmp_path / "reader"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: reader\ndescription: Read\n---\nRead.\n"
    )
    cancelled = asyncio.Event()

    async def wait_for_loader(ctx: Any, *, call: Any, tool_def: Any, args: Any) -> Any:
        if call.tool_name == "load_capability":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return args

    def respond(messages: Any, info: Any) -> ModelResponse:
        loader = next(t for t in info.function_tools if t.name == "load_capability")
        assert loader.tool_kind == "capability-load" and loader.timeout is None
        return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "reader"})])

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[directory],
        tools=[],
        access_check=allow,
        instructions="Read.",
        tool_timeout_seconds=0.01,
    )
    with pytest.raises(TimeoutError):
        await agent.run(
            "load",
            timeout=0.1,
            capabilities=[Hooks(before_tool_execute=wait_for_loader)],
        )
    assert cancelled.is_set()
