"""Tests for Agent.send_proactive_message.

Covers the programmatic, response-only proactive message API: User and
Conversation are resolved/created via the agent's Memory, a new Interaction
is created with empty utterance, and ResponseBus.publish is invoked so the
channel adapter delivers the content and the bus auto-records to
interaction.response.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from jvagent.core.agent import Agent
from jvagent.memory.manager import Memory
from jvagent.memory.user import User


def _agent_name() -> str:
    return f"proactive-test-{uuid.uuid4().hex[:10]}"


async def _make_agent_with_memory() -> Agent:
    agent = await Agent.create(
        namespace="test",
        name=_agent_name(),
        alias="Proactive Test Agent",
        enabled=True,
        description="",
    )
    memory = await Memory.create()
    await agent.connect(memory, direction="both")
    return agent


def _install_publish_spy(agent: Agent) -> AsyncMock:
    """Replace agent._response_bus with one whose publish() is a spy.

    Returns the AsyncMock so the test can inspect call kwargs.
    """

    class _BusStub:
        def __init__(self) -> None:
            self.publish = AsyncMock(return_value=None)

    bus = _BusStub()
    # Bypass the lazy initializer in get_response_bus by setting the cached attr.
    agent._response_bus = bus
    return bus.publish


@pytest.mark.asyncio
async def test_creates_interaction_with_empty_utterance(test_db):
    agent = await _make_agent_with_memory()
    publish_spy = _install_publish_spy(agent)

    interaction = await agent.send_proactive_message(
        user_id="user-1",
        content="Hello there",
        channel="default",
    )

    assert interaction is not None
    assert interaction.utterance == ""
    publish_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_invokes_response_bus_publish_with_expected_kwargs(test_db):
    agent = await _make_agent_with_memory()
    publish_spy = _install_publish_spy(agent)

    interaction = await agent.send_proactive_message(
        user_id="user-1",
        content="Hi",
        channel="whatsapp",
    )

    kwargs = publish_spy.await_args.kwargs
    assert kwargs["channel"] == "whatsapp"
    assert kwargs["user_id"] == "user-1"
    assert kwargs["content"] == "Hi"
    assert kwargs["interaction"] is interaction
    assert kwargs["interaction_id"] == interaction.id
    assert kwargs["category"] == "user"
    assert kwargs["stream"] is False
    assert kwargs["metadata"]["is_proactive"] is True
    assert kwargs["metadata"]["source_action"] == "ProactiveDispatch"


@pytest.mark.asyncio
async def test_tags_is_proactive_in_parameters(test_db):
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    interaction = await agent.send_proactive_message(
        user_id="user-1",
        content="x",
        channel="default",
        source_action="UnitTest",
        metadata={"reason": "manual", "job_id": "j-123"},
    )

    tag = next(p for p in interaction.parameters if p.get("action_name") == "UnitTest")
    assert tag["is_proactive"] is True
    assert tag["reason"] == "manual"
    assert tag["job_id"] == "j-123"


@pytest.mark.asyncio
async def test_bootstraps_user_and_conversation_when_missing(test_db):
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    interaction = await agent.send_proactive_message(
        user_id="brand-new-user",
        content="hi",
        channel="default",
    )

    assert interaction is not None
    memory = await agent.get_memory()
    user = await memory.get_user("brand-new-user", create_if_missing=False)
    assert user is not None
    convs = await user.list_conversations()
    assert len(convs) == 1
    assert convs[0].id == interaction.conversation_id


@pytest.mark.asyncio
async def test_reuses_existing_active_conversation(test_db):
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    memory = await agent.get_memory()
    user = await memory.get_user("user-1", create_if_missing=True)
    pre_existing = await user.create_conversation(channel="default")

    interaction = await agent.send_proactive_message(
        user_id="user-1",
        content="hello",
        channel="default",
    )

    assert interaction is not None
    assert interaction.conversation_id == pre_existing.id
    convs = await user.list_conversations()
    assert len(convs) == 1


@pytest.mark.asyncio
async def test_explicit_session_id_routes_to_matching_conversation(test_db):
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    memory = await agent.get_memory()
    user = await memory.get_user("user-1", create_if_missing=True)
    matched = await user.create_conversation(session_id="sess-A", channel="default")
    await user.create_conversation(session_id="sess-B", channel="default")

    interaction = await agent.send_proactive_message(
        user_id="user-1",
        content="targeted",
        channel="default",
        session_id="sess-A",
    )

    assert interaction is not None
    assert interaction.conversation_id == matched.id


@pytest.mark.asyncio
async def test_returns_none_on_missing_user_id_content_or_channel(test_db):
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    assert (
        await agent.send_proactive_message(user_id="", content="x", channel="c") is None
    )
    assert (
        await agent.send_proactive_message(user_id="u", content="", channel="c") is None
    )
    assert (
        await agent.send_proactive_message(user_id="u", content="x", channel="") is None
    )


@pytest.mark.asyncio
async def test_returns_none_when_memory_missing(test_db):
    agent = await Agent.create(
        namespace="test",
        name=_agent_name(),
        alias="No Memory",
        enabled=True,
        description="",
    )
    _install_publish_spy(agent)

    result = await agent.send_proactive_message(
        user_id="u", content="x", channel="default"
    )
    assert result is None


@pytest.mark.asyncio
async def test_create_user_unique_per_memory(test_db):
    """Second send_proactive_message for same user reuses the User node."""
    agent = await _make_agent_with_memory()
    _install_publish_spy(agent)

    await agent.send_proactive_message(
        user_id="repeat-user", content="first", channel="default"
    )
    await agent.send_proactive_message(
        user_id="repeat-user", content="second", channel="default"
    )

    memory = await agent.get_memory()
    users = await memory.nodes(node=User, user_id="repeat-user")
    assert len(users) == 1
