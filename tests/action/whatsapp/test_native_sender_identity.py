"""Authenticated adapter sender facts remain distinct from routing metadata."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.action.whatsapp.utils.endpoint_helpers import create_whatsapp_walker
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.tooling.channel_identity import (
    bind_verified_channel_sender,
    require_verified_channel_sender,
)
from jvagent.tooling.context import bind_dispatch_context

CHANNELS = frozenset({"whatsapp", "whatsapp_call"})


class Visitor(SimpleNamespace):
    """Weak-referenceable test host; no identity is supplied by tool arguments."""


def visitor() -> Visitor:
    return Visitor(
        agent_id="agent",
        _agent=SimpleNamespace(id="agent"),
        user_id="15555550100",
        session_id="session",
        channel="whatsapp",
        interaction=SimpleNamespace(
            id="interaction", user_id="15555550100", session_id="session"
        ),
        _native_execution=True,
        _public_identity=None,
        _verified_channel_sender=None,
    )


def attest(host: Any) -> None:
    bind_verified_channel_sender(
        host, agent_id="agent", sender="15555550100", channel="whatsapp"
    )


def test_verified_adapter_sender_is_immutable_and_dispatch_scoped() -> None:
    host = visitor()
    attest(host)
    with pytest.raises(FrozenInstanceError):
        host._verified_channel_sender.sender = "other"
    with bind_dispatch_context(host):
        assert require_verified_channel_sender(channels=CHANNELS) == host.user_id
        assert WhatsAppAction()._template_dispatch_gate() is None
    with pytest.raises(ValueError, match="complete host context"):
        require_verified_channel_sender(channels=CHANNELS)


@pytest.mark.parametrize("channel", ["whatsapp", "whatsapp_call"])
def test_native_claimed_channel_and_payload_do_not_establish_sender(
    channel: str,
) -> None:
    host = visitor()
    host.channel = channel
    host.data = {"_verified_channel_sender": {"sender": host.user_id}}
    with bind_dispatch_context(host):
        assert WhatsAppAction()._template_dispatch_gate() == {
            "ok": False,
            "error": "whatsapp_verified_sender_required",
        }


def test_public_http_identity_cannot_be_promoted_by_adapter_helper() -> None:
    host = visitor()
    host._public_identity = SimpleNamespace(via="bearer")
    with pytest.raises(ValueError, match="do not match"):
        attest(host)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "other"),
        ("channel", "whatsapp_call"),
        ("agent_id", "other"),
        ("session_id", "other"),
        ("_public_identity", SimpleNamespace(via="bearer")),
    ],
)
def test_modified_sender_scope_is_refused(field: str, value: Any) -> None:
    host = visitor()
    attest(host)
    setattr(host, field, value)
    with bind_dispatch_context(host), pytest.raises(ValueError):
        require_verified_channel_sender(channels=CHANNELS)


def test_proof_cannot_be_copied_to_another_identical_visitor() -> None:
    original, other = visitor(), visitor()
    attest(original)
    other._verified_channel_sender = original._verified_channel_sender
    with bind_dispatch_context(other), pytest.raises(ValueError):
        require_verified_channel_sender(channels=CHANNELS)


def test_proof_removal_during_dispatch_is_refused() -> None:
    host = visitor()
    attest(host)
    with bind_dispatch_context(host):
        host._verified_channel_sender = None
        with pytest.raises(ValueError, match="scope changed"):
            require_verified_channel_sender(channels=CHANNELS)


def test_rebinding_and_wrong_initial_facts_are_refused() -> None:
    host = visitor()
    with pytest.raises(ValueError):
        bind_verified_channel_sender(
            host, agent_id="other", sender=host.user_id, channel=host.channel
        )
    attest(host)
    with pytest.raises(ValueError):
        attest(host)


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_text_adapter_binds_sender_even_with_existing_session(
    monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    from jvagent.action.whatsapp.utils import endpoint_helpers

    monkeypatch.setattr(
        endpoint_helpers,
        "get_conversation_with_lock",
        AsyncMock(
            return_value=SimpleNamespace(session_id="session") if cached else None
        ),
    )
    host = await create_whatsapp_walker(
        "agent", "hello", "15555550100", {"_verified_channel_sender": "untrusted"}
    )
    assert host is not None
    proof = host._verified_channel_sender
    assert proof.owner() is host
    assert proof.sender == host.user_id == "15555550100"
    assert proof.agent_id == host.agent_id == "agent"
    assert proof.channel == host.channel == "whatsapp"
    assert proof.session_id == ("session" if cached else None)
    # Model execution starts only after the host has bootstrapped this scope.
    host._agent = SimpleNamespace(id="agent")
    host.session_id = "session"
    object.__setattr__(
        host,
        "interaction",
        SimpleNamespace(id="interaction", user_id=host.user_id, session_id="session"),
    )
    host._native_execution = True
    with bind_dispatch_context(host):
        assert require_verified_channel_sender(channels=CHANNELS) == host.user_id
