"""Cancellation must reach public library terminal persistence before host settlement."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import anyio
import pytest

pytest.importorskip("pydantic_ai_harness")
httpx = pytest.importorskip("httpx2")

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
async def test_cancelled_request_has_terminal_library_event(
    tmp_path: Path,
    native: bool,
    streaming: bool,
) -> None:
    entered = asyncio.Event()

    async def respond(messages: Any, info: Any) -> ModelResponse:
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("unreachable")])

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    model = FunctionModel(respond, stream_function=stream, model_name="gpt-4.1")

    async def allow(name: str) -> bool:
        return True

    if native:
        directory = tmp_path / "test"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: test\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
        )
        interaction = await Interaction.create(utterance="cancel test")
        store: Any = InteractionStepStore(interaction)
        agent = build_native_agent(
            model,
            skill_directory=directory,
            tools=[],
            step_store=store,
            access_check=allow,
            instructions="Answer the user",
        )
    else:
        store = InMemoryStepStore()
        agent = Agent(
            model, capabilities=[StepPersistence(store=store, capture_frontier=True)]
        )

    async def events(ctx: Any, source: Any) -> None:
        async for _ in source:
            pass

    task = asyncio.create_task(
        run_native_agent(
            agent,
            "cancel test",
            conversation_id="session",
            run_id="cancelled",
            event_stream_handler=events if streaming else None,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    recorded = await store.list_events(run_id="cancelled")
    assert [e.kind for e in recorded] == [
        "run_started",
        "model_request_started",
        "run_failed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("openai", [False, "chat", "responses"])
@pytest.mark.parametrize("mcp", [False, True])
async def test_http_disconnect_preserves_sdk_terminal_cleanup(
    monkeypatch: Any,
    tmp_path: Path,
    openai: bool | str,
    mcp: bool,
    reported: bool = False,
    embedded: bool = False,
) -> None:
    from jvagent.action.interact import endpoints
    from jvagent.action.response.response_bus import ResponseBus

    entered = asyncio.Event()
    interaction = await Interaction.create(utterance="disconnect test")

    class SlowStore(InteractionStepStore):
        async def _persist(self) -> None:
            # Real PostgreSQL has cancellation checkpoints during each write.
            await asyncio.sleep(0.02)
            await super()._persist()

    store = SlowStore(interaction)

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    async def events(ctx: Any, source: Any) -> None:
        async for _ in source:
            pass

    model = FunctionModel(stream_function=stream, model_name="gpt-4.1")
    if openai:
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        class PendingStream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                if openai == "responses":
                    yield b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_test","object":"response","created_at":0,"model":"gpt-4.1","status":"in_progress","output":[]}}\n\n'
                else:
                    yield b'data: {"id":"test","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                entered.set()
                await asyncio.Event().wait()

        async def request(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=PendingStream(),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(request))
        cls = OpenAIResponsesModel if openai == "responses" else OpenAIChatModel
        model = cls(
            "gpt-4.1",
            provider=OpenAIProvider(api_key="synthetic-test-key", http_client=client),
        )
    toolsets = []
    if mcp:
        client_module = pytest.importorskip("fastmcp.client")
        from fastmcp.client.transports import StdioTransport
        from pydantic_ai.mcp import MCPToolset

        from jvagent.harness.mcp import ReviewedMCPToolset

        client_mcp = client_module.Client(
            StdioTransport(
                command=sys.executable,
                args=[str(Path(__file__).with_name("mcp_fixture.py"))],
                keep_alive=False,
            )
        )
        sdk = MCPToolset(
            client_mcp, prefer_tasks=False, include_instructions=False
        ).prefixed("mcp_records_")
        toolsets.append(
            ReviewedMCPToolset(sdk, frozenset({"mcp_records__lookup_record"}))
        )
    from jvagent.harness.accounting import NativeSpendRecorder
    from jvagent.harness.spend_store import ConversationSpendStore
    from jvagent.memory.conversation import Conversation

    directory = tmp_path / "test"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: test\ndescription: Test\nallowed-tools: [mcp_records__lookup_record]\n---\nAnswer.\n"
        if mcp
        else "---\nname: test\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
    )

    async def allow(name: str) -> bool:
        return True

    conversation = await Conversation.create(session_id="session", user_id="caller")
    agent = build_native_agent(
        model,
        skill_directory=directory,
        tools=[],
        toolsets=toolsets,
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        spend_observer=NativeSpendRecorder(interaction, lock=store.lock),
        access_check=allow,
        instructions="Answer the user",
    )
    bus = ResponseBus()
    bus.native_execution = True
    walker = SimpleNamespace(
        interaction=None,
        conversation=None,
        agent_id="agent",
        user_id="caller",
        session_id="session",
        channel="default",
        response_bus=bus,
        background_actions=[],
        get_report=AsyncMock(return_value=[]),
    )

    async def spawn(host: Any) -> None:
        walker.interaction = interaction
        await run_native_agent(
            agent,
            "disconnect test",
            conversation_id="session",
            run_id="disconnected",
            event_stream_handler=events,
        )

    walker.spawn = spawn
    monkeypatch.setattr(endpoints, "_issue_session_token", AsyncMock(return_value=None))

    async def disconnected() -> bool:
        return reported and entered.is_set()

    request = SimpleNamespace(is_disconnected=disconnected)

    if embedded:
        from jvagent.action.native_harness import NativeHarnessAction

        monkeypatch.setattr(
            "jvagent.action.interact.interact_walker.InteractWalker",
            lambda **kwargs: walker,
        )
        monkeypatch.setattr(
            "jvagent.core.cache.get_cached_agent",
            AsyncMock(
                return_value=SimpleNamespace(
                    id="agent",
                    get_actions=AsyncMock(return_value=[NativeHarnessAction()]),
                )
            ),
        )

    async def consume() -> None:
        if embedded:
            from jvagent.embed import interact_stream

            source = interact_stream(
                agent_id="agent",
                user_id="caller",
                session_id="session",
                utterance="disconnect test",
                is_disconnected=disconnected,
            )
        else:
            source = endpoints._stream_interaction(
                walker, SimpleNamespace(id="agent"), request=request
            )
        async for _ in source:
            pass

    # Includes cold SDK/MCP startup; durability assertions exercise cancellation.
    with anyio.fail_after(30):
        if reported:
            await consume()
        else:
            async with anyio.create_task_group() as group:
                group.start_soon(consume)
                await entered.wait()
                group.cancel_scope.cancel()
    recorded = await store.list_events(run_id="disconnected")
    if embedded:
        from jvagent.embed.interact import _interact_tasks

        assert "session" not in _interact_tasks
    if openai:
        await client.aclose()
    assert [e.kind for e in recorded] == [
        "run_started",
        "model_request_started",
        "run_failed",
    ]


@pytest.mark.asyncio
async def test_embedded_disconnect_waits_for_native_terminal_cleanup(
    monkeypatch: Any, tmp_path: Path
) -> None:
    await test_http_disconnect_preserves_sdk_terminal_cleanup(
        monkeypatch, tmp_path, "responses", True, reported=True, embedded=True
    )


@pytest.mark.asyncio
async def test_reported_disconnect_does_not_recancel_sdk_cleanup(
    monkeypatch: Any, tmp_path: Path
) -> None:
    await test_http_disconnect_preserves_sdk_terminal_cleanup(
        monkeypatch, tmp_path, "responses", True, reported=True
    )
