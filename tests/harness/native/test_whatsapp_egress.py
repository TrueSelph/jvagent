"""Transport authority comes from persisted SDK host facts, not message metadata."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import StepPersistence

from jvagent.action.response.message import ResponseMessage
from jvagent.action.whatsapp.whatsapp_adapter import WhatsAppAdapter
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction
from jvagent.tooling.channel_identity import bind_verified_channel_sender
from jvagent.tooling.context import bind_dispatch_context


class Visitor(SimpleNamespace):
    """An authenticated in-process channel adapter fixture."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["", "unverified", "sender", "session", "agent", "missing"]
)
async def test_native_whatsapp_reply_requires_matching_sdk_sender_registration(
    failure: str,
) -> None:
    interaction = Interaction(
        user_id="15555550100", session_id="session", channel="whatsapp"
    )
    await interaction.save()
    interaction.native_run = {"run_id": "run", "status": "running"}
    host = Visitor(
        agent_id="agent",
        _agent=SimpleNamespace(id="agent"),
        user_id=interaction.user_id,
        session_id="session",
        channel="whatsapp",
        interaction=interaction,
    )
    if failure != "unverified":
        bind_verified_channel_sender(
            host, agent_id="agent", sender=host.user_id, channel="whatsapp"
        )
    store = InteractionStepStore(interaction, agent_id="agent")
    with bind_dispatch_context(host):
        facts = store.registration_metadata()
        agent = Agent(
            TestModel(call_tools=[]),
            capabilities=[StepPersistence(store=store, metadata=facts)],
        )
        await agent.run("Acknowledge", run_id="run", conversation_id="session")
    await interaction.save()
    api = SimpleNamespace(
        send_message=AsyncMock(return_value={"ok": True}),
        set_typing_status=AsyncMock(return_value={"ok": True}),
    )
    action = SimpleNamespace(
        agent_id="other" if failure == "agent" else "agent",
        chunk_length=1024,
        is_configured=lambda: True,
        api=AsyncMock(return_value=api),
    )
    message = ResponseMessage(
        user_id="other" if failure == "sender" else host.user_id,
        session_id="other" if failure == "session" else "session",
        interaction_id="missing" if failure == "missing" else interaction.id,
        content="Acknowledged",
        channel="whatsapp",
        message_type="adhoc",
        metadata={"native_replay": False, "jv_verified_channel_sender": host.user_id},
    )
    assert await WhatsAppAdapter(action=action).send(message) is (not failure)
    assert api.send_message.await_count == (0 if failure else 1)
    assert action.api.await_count == (0 if failure else 1)
    if not failure:
        assert facts["jv_verified_channel_sender"] == host.user_id
    elif failure == "unverified":
        assert "jv_verified_channel_sender" not in facts


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True])
async def test_local_native_publication_cannot_relay_to_an_adapter(local: bool) -> None:
    from jvagent.action.response.response_bus import ResponseBus

    adapter = SimpleNamespace(channel="whatsapp", send=AsyncMock(return_value=True))
    bus = ResponseBus()
    await bus.register_channel_adapter(adapter)
    await bus.publish(
        session_id="session",
        user_id="caller",
        channel="whatsapp",
        content="Hello",
        metadata={"native_local_only": local},
    )
    assert adapter.send.await_count == (0 if local else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["blocked", "unstarted"])
@pytest.mark.parametrize("live", [False, True])
async def test_nonexecuting_notice_needs_live_origin_and_cannot_replay(
    status: str, live: bool
) -> None:
    interaction = Interaction(
        user_id="15555550100", session_id="session", channel="whatsapp"
    )
    interaction.native_run = {"status": "blocked"} if status == "blocked" else {}
    await interaction.save()
    host = Visitor(
        agent_id="agent",
        _agent=SimpleNamespace(id="agent"),
        user_id=interaction.user_id,
        session_id="session",
        channel="whatsapp",
        interaction=interaction,
    )
    bind_verified_channel_sender(
        host, agent_id="agent", sender=host.user_id, channel="whatsapp"
    )
    api = SimpleNamespace(
        send_message=AsyncMock(return_value={"ok": True}), set_typing_status=AsyncMock()
    )
    action = SimpleNamespace(
        agent_id="agent",
        chunk_length=1024,
        is_configured=lambda: True,
        api=AsyncMock(return_value=api),
    )
    adapter = WhatsAppAdapter(action=action)
    message = ResponseMessage(
        user_id=host.user_id,
        session_id="session",
        interaction_id=interaction.id,
        channel="whatsapp",
        content="Recovery is required",
        metadata={"native_replay": False},
    )
    with bind_dispatch_context(host if live else None):
        assert await adapter.send(message) is live
    assert await adapter.send(message) is False
    assert api.send_message.await_count == int(live)
