"""Public caller proof must precede memory ownership and capability issuance."""

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from jvspatial import flush_deferred_entities
from jvspatial.api.exceptions import AuthenticationError

from jvagent.action.interact import session_token as st
from jvagent.memory.conversation import Conversation

SECRET = "native-identity-unit-test-key-" * 2


@pytest.fixture(autouse=True)
def identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", SECRET)
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", "required")

    async def validate(token: str) -> Any:
        # Unit subjects are symbolic; production validation is jvspatial's
        # account/JTI/revocation service, exercised with real users in-browser.
        uid = st.verify_bearer(token)
        return SimpleNamespace(id=uid) if uid else None

    monkeypatch.setattr(
        "jvspatial.api.get_auth_service",
        lambda: SimpleNamespace(validate_token=validate),
    )


def _request(*, bearer: str | None = None, capability: str | None = None) -> Any:
    headers = {}
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    if capability:
        headers["x-session-token"] = capability
    return SimpleNamespace(headers=headers)


def _login(subject: str) -> str:
    return jwt.encode(
        {"user_id": subject, "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"
    )


def _agent(conversation: Any = None) -> Any:
    async def load(session: str) -> Any:
        if conversation is None:
            return None
        return await Conversation.find_one({"id": conversation.id})

    return SimpleNamespace(
        id="agent",
        get_memory=AsyncMock(
            return_value=SimpleNamespace(get_conversation_by_session=load)
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [None, "new-pinned-session"])
async def test_anonymous_subject_is_server_issued_not_a_client_account(
    session: str | None,
) -> None:
    decisions = [
        await st.resolve_interact_identity(
            request=_request(),
            agent=_agent(),
            agent_id="agent",
            session_id=session,
            user_id="victim",
            native=True,
        )
        for _ in range(2)
    ]
    assert all(
        not d.reject and d.public_identity_kind == "anonymous" for d in decisions
    )
    assert all(d.verified_user_id and d.verified_user_id != "victim" for d in decisions)
    assert decisions[0].verified_user_id != decisions[1].verified_user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "log", "invalid"])
async def test_native_cannot_disable_public_enforcement(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", mode)
    d = await st.resolve_interact_identity(
        request=_request(bearer="invalid"),
        agent=_agent(),
        agent_id="agent",
        session_id=None,
        user_id="victim",
        native=True,
    )
    assert d.mode == "required" and d.reject


@pytest.mark.asyncio
async def test_native_without_signing_configuration_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JVSPATIAL_JWT_SECRET_KEY")
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent(),
        agent_id="agent",
        session_id=None,
        user_id=None,
        native=True,
    )
    assert d.reject


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_owner_proof_migrates_unbound_conversation_and_retires_old_capabilities(
    test_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred: bool,
) -> None:
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db.jsondb import JsonDB

    conversation = await Conversation.create(
        session_id="session", user_id="owner", token_secret="old-secret"
    )
    await flush_deferred_entities(conversation, strict=True)
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    host = _agent(conversation)
    old = st.mint_session_token(
        agent_id="agent",
        session_id="session",
        user_id="owner",
        token_secret="old-secret",
    )
    rejected = await st.resolve_interact_identity(
        request=_request(capability=old),
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    assert rejected.reject and rejected.reason == "bind_unbound_identity"
    request = _request(bearer=_login("owner"))
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="spoofed",
        native=True,
    )
    assert decision.verified_user_id == "owner"
    current = await st.bind_public_identity(
        conversation, decision=decision, request=request, agent=host
    )
    assert (
        current.public_identity_kind == "bearer"
        and current.token_secret != "old-secret"
    )
    set_default_context(GraphContext(database=JsonDB(base_path=str(test_db))))
    restored = await Conversation.find_one({"id": conversation.id})
    assert restored is not None and restored.public_identity_kind == "bearer"
    assert restored.token_secret == current.token_secret
    after = await st.resolve_interact_identity(
        request=_request(capability=old),
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    assert after.reject
    assert (
        await st.refresh_session_token(
            request=_request(capability=old), agent=host, agent_id="agent", native=True
        )
    )[0] is None


@pytest.mark.asyncio
async def test_locked_binding_rechecks_secret_after_preflight() -> None:
    conversation = await Conversation.create(
        session_id="session",
        user_id="anonymous",
        public_identity_kind="anonymous",
        token_secret="before",
    )
    token = st.mint_session_token(
        agent_id="agent",
        session_id="session",
        user_id="anonymous",
        token_secret="before",
    )
    host = _agent(conversation)
    request = _request(capability=token)
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id=None,
        native=True,
    )
    assert not decision.reject
    conversation.rotate_token_secret()
    await conversation.save()
    with pytest.raises(AuthenticationError):
        await st.bind_public_identity(
            conversation, decision=decision, request=request, agent=host
        )


@pytest.mark.asyncio
async def test_native_lookup_failure_is_not_anonymous_creation() -> None:
    host = SimpleNamespace(
        get_memory=AsyncMock(side_effect=OSError("graph unavailable"))
    )
    with pytest.raises(OSError, match="graph unavailable"):
        await st.resolve_interact_identity(
            request=_request(),
            agent=host,
            agent_id="agent",
            session_id="existing",
            user_id="victim",
            native=True,
        )


@pytest.mark.asyncio
async def test_proof_is_rechecked_when_interaction_is_created() -> None:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.native_harness import NativeHarnessAction
    from jvagent.action.response.response_bus import ResponseBus
    from jvagent.memory.interaction import Interaction

    conversation = await Conversation.create(
        session_id="session",
        user_id="anonymous",
        public_identity_kind="anonymous",
        token_secret="before",
    )
    host = _agent(conversation)
    memory = await host.get_memory()
    memory.get_session = AsyncMock(
        return_value=(None, conversation, "anonymous", "session", False)
    )
    host.get_actions = AsyncMock(return_value=[NativeHarnessAction()])
    host.get_access_control_action = AsyncMock(return_value=None)
    bus = ResponseBus()
    bus.native_execution = True
    host.get_response_bus = AsyncMock(return_value=bus)
    token = st.mint_session_token(
        agent_id="agent",
        session_id="session",
        user_id="anonymous",
        token_secret="before",
    )
    request = _request(capability=token)
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id=None,
        native=True,
    )
    visitor = InteractWalker(
        agent_id="agent", session_id="session", user_id="anonymous", utterance="hello"
    )
    visitor._public_identity = decision
    visitor._public_identity_request = request
    assert await visitor._bootstrap_interaction(host, through="session") == "ready"
    conversation.rotate_token_secret()
    await conversation.save()
    assert (
        await visitor._bootstrap_interaction(host, through="create")
        == "identity_rejected"
    )
    assert visitor.interaction is None
    assert not await Interaction.find({})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["unbound", "session", "subject", "agent", "channel", "expired"]
)
async def test_native_capability_scope_cannot_expand(case: str) -> None:
    conversation = await Conversation.create(
        session_id="session",
        user_id="anonymous",
        public_identity_kind="anonymous",
        token_secret="secret",
    )
    options: dict[str, Any] = {
        "agent_id": "agent",
        "session_id": "session",
        "user_id": "anonymous",
        "token_secret": "secret",
    }
    if case in {"session", "subject", "agent"}:
        options[
            {"session": "session_id", "subject": "user_id", "agent": "agent_id"}[case]
        ] = "foreign"
    elif case == "expired":
        pass  # Rewrite the signed expiry below; minting clamps nonpositive TTLs.
    elif case == "unbound":
        conversation.public_identity_kind = "unbound"
    else:
        conversation.channel = "whatsapp"
    await conversation.save()
    token = st.mint_session_token(**options)
    if case == "expired":
        claims = jwt.decode(token, SECRET, algorithms=["HS256"])
        claims["exp"] = int(time.time()) - 10
        token = jwt.encode(claims, SECRET, algorithm="HS256")
    decision = await st.resolve_interact_identity(
        request=_request(capability=token),
        agent=_agent(conversation),
        agent_id="agent",
        session_id="session",
        user_id="anonymous",
        native=True,
    )
    assert decision.reject


@pytest.mark.asyncio
async def test_server_authentication_rejection_cannot_fall_back_to_signed_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A signed JWT alone does not establish active account or revocation proof.
    service = SimpleNamespace(validate_token=AsyncMock(return_value=None))
    monkeypatch.setattr("jvspatial.api.get_auth_service", lambda: service)
    token = _login("owner")
    decision = await st.resolve_interact_identity(
        request=_request(bearer=token),
        agent=_agent(),
        agent_id="agent",
        session_id=None,
        user_id="owner",
        native=True,
    )
    assert decision.reject
    service.validate_token.assert_awaited_once_with(token)


@pytest.mark.asyncio
async def test_failed_binding_flush_never_returns_a_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await Conversation.create(session_id="session", user_id="owner")
    request = _request(bearer=_login("owner"))
    host = _agent(conversation)
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    flush = AsyncMock(side_effect=OSError("flush unavailable"))
    monkeypatch.setattr("jvspatial.flush_deferred_entities", flush)
    with pytest.raises(OSError, match="flush unavailable"):
        await st.bind_public_identity(
            conversation, decision=decision, request=request, agent=host
        )


@pytest.mark.asyncio
async def test_binding_rechecks_agent_memory_scope() -> None:
    conversation = await Conversation.create(session_id="session", user_id="owner")
    request = _request(bearer=_login("owner"))
    host = _agent(conversation)
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    with pytest.raises(AuthenticationError):
        await st.bind_public_identity(
            conversation, decision=decision, request=request, agent=_agent()
        )


@pytest.mark.asyncio
async def test_locked_binding_rechecks_account_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await Conversation.create(session_id="session", user_id="owner")
    request = _request(bearer=_login("owner"))
    host = _agent(conversation)
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    monkeypatch.setattr(
        "jvspatial.api.get_auth_service",
        lambda: SimpleNamespace(
            validate_token=AsyncMock(return_value=None),
        ),
    )
    with pytest.raises(AuthenticationError):
        await st.bind_public_identity(
            conversation, decision=decision, request=request, agent=host
        )
    assert conversation.public_identity_kind == "unbound"


@pytest.mark.asyncio
async def test_session_open_checks_entry_policy_before_issuing_ownership() -> None:
    from jvagent.action.access_control import AccessControlAction
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.native_harness import NativeHarnessAction
    from jvagent.action.response.response_bus import ResponseBus
    from jvagent.memory.interaction import Interaction

    conversation = await Conversation.create(session_id="session", user_id="owner")
    host = _agent(conversation)
    memory = await host.get_memory()
    memory.get_session = AsyncMock(
        return_value=(None, conversation, "owner", "session", False)
    )
    host.get_actions = AsyncMock(return_value=[NativeHarnessAction()])
    policy = AccessControlAction(
        allow_anonymous=False, default_deny=True, permissions={}
    )
    host.get_access_control_action = AsyncMock(return_value=policy)
    bus = ResponseBus()
    bus.native_execution = True
    host.get_response_bus = AsyncMock(return_value=bus)
    request = _request(bearer=_login("owner"))
    decision = await st.resolve_interact_identity(
        request=request,
        agent=host,
        agent_id="agent",
        session_id="session",
        user_id="owner",
        native=True,
    )
    walker = InteractWalker(agent_id="agent", user_id="owner", session_id="session")
    walker._public_identity = decision
    walker._public_identity_request = request
    assert await walker._bootstrap_interaction(host, through="open") == "access_denied"
    assert conversation.public_identity_kind == "unbound"
    assert walker.interaction is None and not await Interaction.find({})
