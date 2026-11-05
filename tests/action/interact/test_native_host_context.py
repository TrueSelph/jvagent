"""Explicit host policy must never downgrade into an ordinary native turn."""

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.response.response_bus import ResponseBus
from jvagent.core.host_context import sign_host_system_context
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_interview_cleanup_does_not_reopen_consumed_host_nonce(
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
) -> None:
    from jvspatial import flush_deferred_entities

    from jvagent.action.interview.session import clear_interview_context

    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", "host-context-test-" * 4)
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    host = await _host()
    await flush_deferred_entities(
        host.get_memory.return_value.get_session.return_value[1], strict=True
    )
    data = sign_host_system_context(
        "Trusted policy", agent_id="agent", user_id="caller", session_id="session"
    )
    first = _walker(data)
    assert await first._bootstrap_interaction(host, through="session") == "ready"
    first.conversation.context["interview"] = {"status": "completed"}
    first.conversation.context["scratch"] = "discard"
    clear_interview_context(first.conversation)
    await first.conversation.save()
    await flush_deferred_entities(first.conversation, strict=True)
    replay = _walker(data)
    assert (
        await replay._bootstrap_interaction(host, through="session")
        == "invalid_host_context"
    )
    assert replay.interaction is None
    assert (
        "interview" not in first.conversation.context
        and "scratch" not in first.conversation.context
    )


async def _host() -> SimpleNamespace:
    conversation = Conversation(session_id="session", user_id="caller")
    await conversation.save()
    bus = ResponseBus()
    bus.native_execution = True
    return SimpleNamespace(
        id="agent",
        get_actions=AsyncMock(return_value=[NativeHarnessAction()]),
        get_memory=AsyncMock(
            return_value=SimpleNamespace(
                get_session=AsyncMock(
                    return_value=(None, conversation, "caller", "session", False)
                )
            )
        ),
        get_response_bus=AsyncMock(return_value=bus),
        get_access_control_action=AsyncMock(return_value=None),
    )


def _walker(data: dict[str, Any]) -> InteractWalker:
    return InteractWalker(
        agent_id="agent",
        user_id="caller",
        session_id="session",
        utterance="A non-empty request must not bypass host policy validation.",
        data=data,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "null",
        "signature",
        "expired",
        "agent",
        "user",
        "session",
        "request",
        "storage",
        "missing_secret",
    ],
)
async def test_invalid_supplied_context_is_rejected_before_interaction(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", "host-context-test-" * 4)
    overrides: dict[str, Any] = {
        "agent_id": "agent",
        "user_id": "caller",
        "session_id": "session",
    }
    if case in {"agent", "user", "session"}:
        overrides[
            {"agent": "agent_id", "user": "user_id", "session": "session_id"}[case]
        ] = "foreign"
    if case == "expired":
        overrides["now"] = int(time.time()) - 301
    data = sign_host_system_context("Trusted host policy", **overrides)
    if case == "malformed":
        data["host_system_context"] = {"body": "malformed", "signature": "invalid"}
    elif case == "null":
        data["host_system_context"] = None
    elif case == "signature":
        data["host_system_context"]["signature"] = "invalid"
    elif case == "request":
        data["run_id"] = "foreign-request"
    elif case == "missing_secret":
        monkeypatch.delenv("JVAGENT_HOST_CONTEXT_SECRET")
    host = await _host()
    if case == "storage":
        monkeypatch.setattr(
            Conversation,
            "find_one",
            AsyncMock(side_effect=OSError("storage unavailable")),
        )
    visitor = _walker(data)
    result = await visitor.initialize_interaction(host)
    assert not result.ok
    assert result.code == "invalid_host_context"
    assert visitor.interaction is None
    assert visitor._host_system_context is None
    assert not await Interaction.find({})


@pytest.mark.asyncio
async def test_valid_context_is_single_use_even_with_nonempty_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", "host-context-test-" * 4)
    data = sign_host_system_context(
        "Trusted host policy", agent_id="agent", user_id="caller", session_id="session"
    )
    host = await _host()
    first = _walker(data)
    assert (await first.initialize_interaction(host)).ok
    assert first._host_system_context == "Trusted host policy"
    replay = _walker(data)
    result = await replay.initialize_interaction(host)
    assert not result.ok
    assert result.code == "invalid_host_context"
    assert replay.interaction is None
    assert len(await Interaction.find({})) == 1


@pytest.mark.asyncio
async def test_ordinary_native_request_does_not_require_a_host_envelope() -> None:
    host = await _host()
    visitor = _walker({})
    assert (await visitor.initialize_interaction(host)).ok
    assert visitor._host_system_context is None


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("flush_fails", [False, True])
async def test_nonce_is_committed_before_policy_promotion(
    test_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
    flush_fails: bool,
) -> None:
    from jvspatial import flush_deferred_entities
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.jsondb import JsonDB

    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", "host-context-test-" * 4)
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    host = await _host()
    conversation = host.get_memory.return_value.get_session.return_value[1]
    await flush_deferred_entities(conversation, strict=True)
    if flush_fails:
        monkeypatch.setattr(
            Conversation, "flush", AsyncMock(side_effect=OSError("flush unavailable"))
        )
    data = sign_host_system_context(
        "Trusted host policy", agent_id="agent", user_id="caller", session_id="session"
    )
    visitor = _walker(data)
    code = await visitor._bootstrap_interaction(host, through="session")
    if flush_fails:
        assert code == "invalid_host_context"
        assert visitor._host_system_context is None
        return
    assert code == "ready"
    assert (
        visitor.interaction is None
    )  # no model/run persistence can mask a missing nonce
    assert visitor._host_system_context == "Trusted host policy"
    set_default_context(GraphContext(database=JsonDB(base_path=str(test_db))))
    restored = await Conversation.find_one({"id": conversation.id})
    assert restored is not None
    ledger = restored.context.get("_jvagent_host_context_nonces", {})
    assert len(ledger) == 1
    assert list(ledger.values())[0]["correlation_id"] == visitor.correlation_id
