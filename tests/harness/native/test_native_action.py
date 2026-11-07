"""Native ingress and delivery boundary regressions."""

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.native_harness import NativeHarnessAction


@pytest.mark.asyncio
async def test_pre_model_admission_error_has_visible_notice(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        NativeHarnessAction,
        "_execute",
        AsyncMock(side_effect=ValueError("private configuration detail")),
    )
    interaction = SimpleNamespace(
        native_run={},
        has_emitted=lambda: False,
        save=AsyncMock(),
        compute_usage=lambda: {},
    )
    publish = AsyncMock(return_value=True)
    facade = SimpleNamespace(
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish))
    )
    visitor = SimpleNamespace(interaction=interaction)
    await NativeHarnessAction.execute(facade, visitor)
    assert interaction.native_run == {"status": "blocked", "reason": "admission_failed"}
    interaction.save.assert_awaited_once()
    assert "configuration prevented" in publish.call_args.args[0]
    assert "private configuration detail" not in publish.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize(
    "delivered,settled",
    [
        (True, True),
        (False, True),
        (None, True),
        (None, False),
        ("cancel_before_delivery", False),
        ("intent_write_failed", True),
        ("settlement_write_failed", True),
        ("pre_registration_failure", True),
        ("mutation_denied", True),
        ("mutation_unknown", True),
        ("retry_limit", True),
        ("wrapped_retry_limit", True),
        ("protocol_error", True),
    ],
)
async def test_native_receipt_precedes_delivery(
    monkeypatch: Any,
    delivered: bool | str | None,
    settled: bool,
    caplog: Any,
    attached: bool,
    profile: bool,
) -> None:
    from jvagent.action.native_harness import native_harness_action as module

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        from jvagent.tooling.context import get_dispatch_context, get_tool_visitor

        assert get_tool_visitor() is visitor
        assert get_dispatch_context().user_id == "user"
        content = next(
            part.content
            for part in messages[-1].parts
            if isinstance(part, UserPromptPart)
        )
        if attached:
            assert content[0] == "hello"
            assert content[1] == "Attached file: sample.png"
            assert isinstance(content[2], BinaryContent)
            assert content[2].data == b"\x89PNG\r\n"
        else:
            assert content == "hello"
        if delivered == "cancel_before_delivery":
            raise asyncio.CancelledError()
        yield "ok"

    runtime = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("ok")]),
            stream_function=stream,
        )
    )
    from jvagent.tooling.tool import Tool

    original_tool = Tool("visitor_status", "Scoped status", effect_class="read")

    def factory(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["instructions"] == (
            "Trusted host policy\n\nHost attachment input policy: image/png. "
            "Files outside this MIME list cannot be accepted."
            "\n\nImage uploads: enabled. PDF uploads: disabled. "
            "Do not suggest uploads in a disabled format."
        )
        assert kwargs["model_settings"] == {"temperature": 0.0, "max_tokens": 8192}
        assert kwargs["max_run_cost_usd"] == ("0.75" if profile else "2")
        assert kwargs["tools"][0].binds_visitor is True
        assert original_tool.binds_visitor is None
        return runtime

    monkeypatch.setattr(module, "build_native_agent", factory)
    real_run = module.run_native_agent

    async def selected_run(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["max_requests"] == (1 if profile else 2)
        assert kwargs["timeout_seconds"] == (3 if profile else 10)
        assert kwargs["max_cost_usd"] == ("0.75" if profile else "2")
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(module, "run_native_agent", selected_run)
    if delivered == "pre_registration_failure":
        monkeypatch.setattr(
            module,
            "run_native_agent",
            AsyncMock(side_effect=ValueError("private provider credential detail")),
        )
    if delivered == "mutation_denied":
        from jvagent.harness.mutation_tools import MutationAccessDenied

        monkeypatch.setattr(
            module,
            "run_native_agent",
            AsyncMock(side_effect=MutationAccessDenied("private policy detail")),
        )
    if delivered == "mutation_unknown":
        from jvagent.harness.mutation_tools import MutationOutcomeUnknown

        monkeypatch.setattr(
            module,
            "run_native_agent",
            AsyncMock(side_effect=MutationOutcomeUnknown("private provider detail")),
        )
    if delivered in {"retry_limit", "wrapped_retry_limit", "protocol_error"}:
        from pydantic_ai.exceptions import (
            ModelRetry,
            ToolRetryError,
            UnexpectedModelBehavior,
        )
        from pydantic_ai.messages import ToolReturnPart

        error = UnexpectedModelBehavior("private SDK retry detail")
        error.__cause__ = (
            ToolRetryError(
                ToolReturnPart(
                    tool_name="lookup",
                    content="private tool argument detail",
                    tool_call_id="call",
                    outcome="retried",
                )
            )
            if delivered == "wrapped_retry_limit"
            else (
                ValueError("private protocol detail")
                if delivered == "protocol_error"
                else ModelRetry("private tool argument detail")
            )
        )
        monkeypatch.setattr(module, "run_native_agent", AsyncMock(side_effect=error))
    if not settled:
        monkeypatch.setattr(
            module, "persist_cancelled_receipt", AsyncMock(return_value=False)
        )
    saved = []
    interaction = SimpleNamespace(
        native_run={},
        native_steps={},
        observability_metrics=[],
        compute_usage=lambda: {},
        has_emitted=lambda: False,
        get_previous_interaction=AsyncMock(return_value=None),
    )

    async def save() -> None:
        saved.append(dict(interaction.native_run))

    interaction.save = save
    published: list[str] = []
    if delivered in {"intent_write_failed", "settlement_write_failed"}:
        original_flush = module.flush_deferred_entities

        async def flush(entity: Any, *, strict: bool) -> None:
            status = entity.native_run.get("status")
            if (
                delivered == "intent_write_failed" and status == "pending_delivery"
            ) or (delivered == "settlement_write_failed" and status == "delivered"):
                raise OSError("Synthetic graph acknowledgement failure")
            await original_flush(entity, strict=strict)

        monkeypatch.setattr(module, "flush_deferred_entities", flush)

    async def publish(text: str, **kwargs: Any) -> bool:
        published.append(text)
        assert kwargs["message_id"].startswith("o.ResponseMessage.")
        if delivered in {
            "pre_registration_failure",
            "mutation_denied",
            "mutation_unknown",
            "retry_limit",
            "wrapped_retry_limit",
            "protocol_error",
        }:
            assert saved[-1]["status"] == "failed"
            return True
        assert saved[-1]["status"] == "pending_delivery"
        assert saved[-1]["usage"]["requests"] == 1
        if delivered is None:
            raise asyncio.CancelledError()
        return bool(delivered)

    owner = SimpleNamespace(
        id="owner",
        binds_tools_to_visitor=True,
        get_manifest=lambda: SimpleNamespace(pattern_orchestrator=False),
        get_tools=AsyncMock(return_value=[original_tool]),
        get_class_name=lambda: "VisitorAction",
    )
    facade = SimpleNamespace(
        id="native",
        get_agent=AsyncMock(
            return_value=SimpleNamespace(
                id="agent", get_actions=AsyncMock(return_value=[owner])
            )
        ),
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish)),
        max_run_cost_usd="2",
        max_conversation_cost_usd="20",
        model="test",
        model_settings={"temperature": 0.0, "max_tokens": 8192},
        input_media_types=["image/png"],
        skill_directory=".",
        delegates=[],
        instructions="",
        context_max_tokens=32000,
        context_keep_tokens=16000,
        max_requests=2,
        max_tool_calls=40,
        max_tokens=2000,
        max_output_tokens=None,
        timeout_seconds=10,
        channel_limits=(
            {
                "web": {
                    "max_requests": 1,
                    "timeout_seconds": 3,
                    "max_run_cost_usd": "0.75",
                }
            }
            if profile
            else {}
        ),
    )
    visitor = SimpleNamespace(
        interaction=interaction,
        conversation=SimpleNamespace(id="conversation", native_spend={}),
        user_id="user",
        session_id="session",
        utterance="hello",
        data=(
            {
                "image_urls": [
                    {
                        "base64": "iVBORw0K",
                        "mime_type": "image/png",
                        "filename": "sample.png",
                    }
                ]
            }
            if attached
            else {}
        ),
        channel="web",
        correlation_id="corr-server-owned",
        _host_system_context="Trusted host policy",
    )
    if delivered == "protocol_error":
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1].get("reason") != "retry_limit"
        assert published == [
            "The model request could not complete. Please check the service configuration."
        ]
        assert (
            "private protocol detail" not in str(saved) + str(published) + caplog.text
        )
    elif delivered in {"retry_limit", "wrapped_retry_limit"}:
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["reason"] == "retry_limit"
        assert published == [
            "The agent reached its retry limit while trying to complete this request. Please try a revised request."
        ]
        assert (
            "private SDK retry detail" not in str(saved) + str(published) + caplog.text
        )
        assert (
            "private tool argument detail"
            not in str(saved) + str(published) + caplog.text
        )
    elif delivered == "mutation_unknown":
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["reason"] == "mutation_outcome_unknown"
        assert published == [
            "That operation may already have been accepted. Automatic retry "
            "is blocked until its outcome has been reconciled."
        ]
        assert (
            "private provider detail" not in str(saved) + str(published) + caplog.text
        )
    elif delivered == "mutation_denied":
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["failure_type"] == "MutationAccessDenied"
        assert saved[-1]["reason"] == "mutation_access_denied"
        assert published == [
            "Your current permissions do not allow that write operation."
        ]
        assert "private policy detail" not in str(saved) + str(published) + caplog.text
    elif delivered == "pre_registration_failure":
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["failure_type"] == "ValueError"
        assert "native_execution_failed" in caplog.text
        assert (
            "private provider credential detail"
            not in str(saved) + str(published) + caplog.text
        )
        assert len(published) == 1
    elif delivered in {"intent_write_failed", "settlement_write_failed"}:
        with pytest.raises(OSError, match="acknowledgement"):
            await NativeHarnessAction.execute(facade, visitor)
        assert interaction.native_run["status"] == (
            "pending_delivery"
            if delivered == "intent_write_failed"
            else "delivery_unknown"
        )
        assert published == ([] if delivered == "intent_write_failed" else ["ok"])
    elif delivered == "cancel_before_delivery":
        with pytest.raises(asyncio.CancelledError):
            await NativeHarnessAction.execute(facade, visitor)
        assert interaction.native_run["status"] == "blocked"
        assert interaction.native_run["reason"] == "cancellation_receipt_unconfirmed"
        assert saved[-1]["status"] == "running"
    elif delivered is None:
        with pytest.raises(asyncio.CancelledError):
            await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == (
            "delivery_unknown" if settled else "pending_delivery"
        )
        assert saved[-1]["output"] == "ok"
        assert saved[-1]["usage_complete"] is True
        if not settled:
            assert interaction.native_run["status"] == "delivery_unknown"
            assert (
                interaction.native_run["reason"] == "cancellation_receipt_unconfirmed"
            )
    elif delivered:
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "delivered"
    else:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "pending_delivery"
    from jvagent.tooling.context import get_dispatch_context, get_tool_visitor

    assert interaction.native_run["run_id"] == "corr-server-owned"
    assert interaction.native_run["limits"]["max_requests"] == (1 if profile else 2)
    assert interaction.native_run["limits"]["max_run_cost_usd"] == (
        "0.75" if profile else "2"
    )
    assert get_dispatch_context() is None
    assert get_tool_visitor() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_repeated_cancellation_preserves_caller_and_receipt(
    write_fails: bool,
) -> None:
    import asyncio

    from jvagent.action.native_harness.native_harness_action import (
        persist_cancelled_receipt,
    )

    entered = asyncio.Event()
    release = asyncio.Event()
    saved = []
    interaction = SimpleNamespace(
        native_run={"run_id": "cancelled-run", "status": "cancelled"}
    )

    async def save() -> None:
        entered.set()
        await release.wait()
        if write_fails:
            raise OSError("graph unavailable")
        saved.append(dict(interaction.native_run))

    interaction.save = save

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await persist_cancelled_receipt(interaction)
            raise

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    task.cancel()
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bool(saved) is not write_fails


@pytest.mark.asyncio
async def test_dispatch_context_is_task_local_and_legacy_compatible() -> None:
    from jvagent.tooling.context import bind_dispatch_context, get_dispatch_context
    from jvagent.tooling.tool_executor import get_tool_visitor

    async def identity(user: str) -> str:
        visitor = SimpleNamespace(user_id=user, session_id="session", channel="web")
        with bind_dispatch_context(visitor):
            await asyncio.sleep(0)
            assert get_tool_visitor() is visitor
            return get_dispatch_context().user_id

    assert await asyncio.gather(identity("first"), identity("second")) == [
        "first",
        "second",
    ]
    assert get_dispatch_context() is None
    assert get_tool_visitor() is None


@pytest.mark.asyncio
async def test_unqualified_predecessor_is_visibly_blocked_without_a_model_call(
    monkeypatch: Any,
) -> None:
    from jvagent.action.native_harness import native_harness_action as module

    def respond(messages: Any, info: Any) -> ModelResponse:
        raise AssertionError("Blocked recovery must not call a model")

    monkeypatch.setattr(
        module,
        "build_native_agent",
        lambda *args, **kwargs: Agent(FunctionModel(respond)),
    )
    interaction = SimpleNamespace(
        native_run={},
        native_steps={},
        observability_metrics=[],
        has_emitted=lambda: False,
        save=AsyncMock(),
        get_previous_interaction=AsyncMock(
            return_value=SimpleNamespace(id="previous", native_run={})
        ),
        compute_usage=lambda: {},
    )
    publish = AsyncMock(return_value=True)
    facade = SimpleNamespace(
        get_agent=AsyncMock(
            return_value=SimpleNamespace(
                id="agent", get_actions=AsyncMock(return_value=[])
            )
        ),
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish)),
        model="test",
        model_settings={},
        skill_directory=".",
        delegates=[],
        instructions="",
        max_run_cost_usd="2",
        max_conversation_cost_usd="20",
        context_max_tokens=32000,
        context_keep_tokens=16000,
    )
    visitor = SimpleNamespace(
        interaction=interaction,
        conversation=SimpleNamespace(id="conversation", native_spend={}),
        user_id="caller",
        session_id="session",
        channel="web",
    )
    await NativeHarnessAction.execute(facade, visitor)
    assert interaction.native_run["status"] == "blocked"
    assert interaction.native_run["predecessor_interaction_id"] == "previous"
    assert "needs recovery" in publish.await_args.args[0]
    assert not interaction.native_steps


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore_cancel", [False, True])
async def test_cancellation_settlement_has_deadline_even_when_write_stalls(
    monkeypatch: pytest.MonkeyPatch, ignore_cancel: bool
) -> None:
    from jvagent.action.native_harness import native_harness_action as native

    monkeypatch.setattr(native, "_CANCELLATION_SETTLEMENT_SECONDS", 0.02, raising=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    write_exited = asyncio.Event()
    interaction = SimpleNamespace(
        native_run={"run_id": "stalled", "status": "cancelled"}
    )

    async def save() -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            if ignore_cancel:
                await release.wait()
            else:
                raise
        finally:
            write_exited.set()

    interaction.save = save
    outcomes = []

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            outcomes.append(await native.persist_cancelled_receipt(interaction))
            raise

    task = asyncio.create_task(run())
    try:
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()  # repeated cancellation must not reset the deadline
        done, _ = await asyncio.wait({task}, timeout=0.2)
        assert done, "Cancellation cleanup is still waiting on the graph"
        assert outcomes == [False]
        if ignore_cancel:
            assert not write_exited.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(write_exited.wait(), 1)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stalled_cancellation_writes_are_capacity_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvagent.action.native_harness import native_harness_action as native

    monkeypatch.setattr(native, "_CANCELLATION_SETTLEMENT_SECONDS", 0.02)
    monkeypatch.setattr(native, "_MAX_CANCELLATION_WRITES", 1)
    release = asyncio.Event()
    exited = asyncio.Event()

    async def save() -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            exited.set()
        raise OSError("late write failure")

    interaction = SimpleNamespace(native_run={"run_id": "one"}, save=save)
    blocked = SimpleNamespace(native_run={"run_id": "two"}, save=AsyncMock())
    try:
        assert await native.persist_cancelled_receipt(interaction) is False
        assert len(native._pending_cancellation_writes) == 1
        assert await native.persist_cancelled_receipt(blocked) is False
        blocked.save.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(exited.wait(), 1)
        await asyncio.sleep(0)
    assert not native._pending_cancellation_writes


@pytest.mark.asyncio
async def test_early_cancellation_flushes_deferred_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvagent.action.native_harness.native_harness_action import (
        persist_cancelled_receipt,
    )
    from jvagent.memory.interaction import Interaction

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "true")
    interaction = Interaction(utterance="cancel before native registration")
    interaction.native_run = {"run_id": "early", "status": "cancelled"}
    assert await persist_cancelled_receipt(interaction) is True
    restored = (await Interaction.find({"id": interaction.id}))[0]
    assert restored.native_run == interaction.native_run
    assert not interaction.deferred_saves_enabled


@pytest.mark.asyncio
async def test_cancellation_persists_unknown_provider_usage_before_next_turn() -> None:
    from pydantic_ai_harness.step_persistence import StepEvent

    from jvagent.action.native_harness.native_harness_action import (
        persist_cancelled_receipt,
    )
    from jvagent.harness.step_store import InteractionStepStore
    from jvagent.memory.interaction import Interaction

    interaction = Interaction(utterance="cancelled provider request")
    interaction.native_run = {"run_id": "cancelled", "status": "cancelled"}
    await InteractionStepStore(interaction).append_event(
        StepEvent(run_id="cancelled", kind="model_request_started", step_index=1)
    )
    assert await persist_cancelled_receipt(interaction) is True
    restored = (await Interaction.find({"id": interaction.id}))[0]
    assert restored.usage["unknown_cost_call_count"] == 1
    assert restored.usage["model_attempt_count"] == 1
    assert restored.usage["cancelled_model_attempt_count"] == 1
