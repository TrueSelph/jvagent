"""Session capability tokens for the public interact endpoint (ADR-0020):
mint/verify, claim→conversation binding, staged-mode resolution, and the
pre-spawn identity guard (Mode A bearer / Mode B token / create / resume)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Optional

import jwt
import pytest

from jvagent.action.interact import session_token as st

_SECRET = "unit-test-secret-key-aaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", _SECRET)
    # default to required so verification paths are exercised; individual tests
    # override as needed.
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", "required")
    yield


def _conv(**kw):
    base = dict(channel="default", session_id="s1", user_id="u1", token_secret="cs1")
    base.update(kw)
    return SimpleNamespace(**base)


def _request(*, bearer: Optional[str] = None, token: Optional[str] = None):
    headers = {}
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    if token is not None:
        headers["x-session-token"] = token
    return SimpleNamespace(headers=headers)


def _agent_with(conversation: Any):
    async def get_conversation_by_session(session_id):
        if conversation is not None and conversation.session_id == session_id:
            return conversation
        return None

    memory = SimpleNamespace(get_conversation_by_session=get_conversation_by_session)

    async def get_memory():
        return memory

    return SimpleNamespace(get_memory=get_memory)


# --- mint / verify ----------------------------------------------------------


def test_mint_and_verify_roundtrip():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    claims, err = st.verify_session_token(tok, expected_agent_id="a1")
    assert err is None
    assert claims["user_id"] == "u1" and claims["cs"] == "cs1"
    assert claims["channel"] == "web" and claims["typ"] == "interact_session"


def test_verify_rejects_expired():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1", ttl_seconds=1
    )
    payload = jwt.decode(tok, _SECRET, algorithms=["HS256"])
    payload["exp"] = int(time.time()) - 10
    stale = jwt.encode(payload, _SECRET, algorithm="HS256")
    claims, err = st.verify_session_token(stale)
    assert claims is None and err == "expired"


def test_verify_rejects_tampered_signature():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    forged = jwt.encode(
        jwt.decode(tok, _SECRET, algorithms=["HS256"]),
        "other-secret",
        algorithm="HS256",
    )
    claims, err = st.verify_session_token(forged)
    assert claims is None and err and err.startswith("invalid")


def test_verify_rejects_agent_mismatch():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    claims, err = st.verify_session_token(tok, expected_agent_id="a2")
    assert claims is None and err == "agent_mismatch"


def test_no_secret_disables_mint(monkeypatch):
    monkeypatch.delenv("JVSPATIAL_JWT_SECRET_KEY", raising=False)
    assert (
        st.mint_session_token(
            agent_id="a", session_id="s", user_id="u", token_secret="c"
        )
        is None
    )
    claims, err = st.verify_session_token("x")
    assert claims is None and err == "no_secret_configured"


# --- claims_match_conversation ---------------------------------------------


def _claims():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    return st.verify_session_token(tok)[0]


def test_bind_ok():
    assert st.claims_match_conversation(_claims(), _conv()) is None


def test_bind_cross_channel_rejected():
    assert st.claims_match_conversation(_claims(), _conv(channel="whatsapp")) == (
        "cross_channel"
    )


def test_bind_session_and_user_mismatch():
    assert st.claims_match_conversation(_claims(), _conv(session_id="other")) == (
        "session_mismatch"
    )
    assert st.claims_match_conversation(_claims(), _conv(user_id="other")) == (
        "user_mismatch"
    )


def test_bind_rotated_secret_rejected():
    assert st.claims_match_conversation(_claims(), _conv(token_secret="rotated")) == (
        "secret_mismatch"
    )


def test_bind_missing_secret_is_soft_miss():
    assert st.claims_match_conversation(_claims(), _conv(token_secret="")) == (
        "no_token_secret"
    )


# --- bearer (Mode A) --------------------------------------------------------


def test_verify_bearer_reads_user_id():
    login = jwt.encode(
        {"user_id": "alice", "exp": int(time.time()) + 60}, _SECRET, algorithm="HS256"
    )
    assert st.verify_bearer(login) == "alice"


def test_verify_bearer_rejects_session_token():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    # a session capability token must not satisfy the Mode A bearer door
    assert st.verify_bearer(tok) is None


# --- staged mode resolution -------------------------------------------------


def test_auth_mode_resolution(monkeypatch):
    for raw, expected in [
        ("off", "off"),
        ("log", "log"),
        ("required", "required"),
        ("REQUIRED", "required"),
        ("bogus", "off"),
        ("", "off"),
    ]:
        monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", raw)
        assert st.auth_mode() == expected


# --- resolve_interact_identity (the pre-spawn guard) ------------------------


async def test_guard_off_mode_is_noop(monkeypatch):
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", "off")
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent_with(None),
        agent_id="a1",
        session_id="s1",
        user_id="u1",
    )
    assert d.mode == "off" and d.reject is False and d.verified_user_id is None


async def test_guard_bearer_overrides_user_id():
    login = jwt.encode(
        {"user_id": "alice", "exp": int(time.time()) + 60}, _SECRET, algorithm="HS256"
    )
    d = await st.resolve_interact_identity(
        request=_request(bearer=login),
        agent=_agent_with(None),
        agent_id="a1",
        session_id=None,
        user_id="spoofed",
    )
    assert d.via == "bearer" and d.verified_user_id == "alice" and not d.reject


async def test_guard_bearer_not_owner_rejected_in_required():
    login = jwt.encode(
        {"user_id": "mallory", "exp": int(time.time()) + 60}, _SECRET, algorithm="HS256"
    )
    d = await st.resolve_interact_identity(
        request=_request(bearer=login),
        agent=_agent_with(_conv(user_id="alice")),
        agent_id="a1",
        session_id="s1",
        user_id=None,
    )
    assert d.denial and d.reason == "bearer_not_owner" and d.reject is True


async def test_guard_create_path_allowed():
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent_with(None),
        agent_id="a1",
        session_id=None,
        user_id=None,
    )
    assert d.reason == "create" and not d.reject


async def test_guard_resume_without_token_rejected_in_required():
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent_with(_conv()),
        agent_id="a1",
        session_id="s1",
        user_id="u1",
    )
    assert d.reason == "missing_session_token" and d.reject is True


async def test_guard_resume_without_token_only_observed_in_log(monkeypatch):
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", "log")
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent_with(_conv()),
        agent_id="a1",
        session_id="s1",
        user_id="u1",
    )
    assert d.denial is True and d.reject is False  # log observes, never rejects


async def test_guard_resume_with_valid_token_ok():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    d = await st.resolve_interact_identity(
        request=_request(token=tok),
        agent=_agent_with(_conv()),
        agent_id="a1",
        session_id="s1",
        user_id="u1",
    )
    assert d.via == "session_token" and d.verified_user_id == "u1" and not d.reject


async def test_guard_cross_channel_resume_rejected():
    tok = st.mint_session_token(
        agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1"
    )
    d = await st.resolve_interact_identity(
        request=_request(token=tok),
        agent=_agent_with(_conv(channel="whatsapp")),
        agent_id="a1",
        session_id="s1",
        user_id="u1",
    )
    assert d.reason == "bind_cross_channel" and d.reject is True


async def test_guard_resume_new_session_allowed():
    # session_id supplied but no existing conversation -> pinned-id create
    d = await st.resolve_interact_identity(
        request=_request(),
        agent=_agent_with(None),
        agent_id="a1",
        session_id="brand-new",
        user_id=None,
    )
    assert d.reason == "resume_new" and not d.reject


# --- non-streaming response surfaces the token (regression) -----------------


def test_interact_response_schema_declares_session_token():
    """The non-streaming JSON path must surface ``session_token``.

    Regression: the generated response model uses ``extra="ignore"``, so any
    field the endpoint returns but does not *declare* is silently dropped on
    serialization. Before the fix the token only appeared on the streaming SSE
    path (raw, no response model) — ``stream=False`` callers never saw it.
    """
    from typing import Optional

    from jvagent.action.interact.endpoints import interact_endpoint

    cfg = getattr(interact_endpoint, "_jvspatial_endpoint_config", None)
    assert cfg is not None
    schema = cfg.get("response")
    assert schema is not None and "session_token" in (schema.data or {})

    model = schema.to_pydantic_model("InteractResponseTest")

    # A populated token (web channel, auth != off) survives serialization.
    with_token = model(
        success=True,
        user_id="u1",
        session_id="s1",
        response="hi",
        session_token="tok123",
    ).model_dump(exclude_none=True)
    assert with_token.get("session_token") == "tok123"

    # No token (off mode / non-web channel) is omitted, not emitted as null.
    without_token = model(
        success=True, user_id="u1", session_id="s1", response="hi"
    ).model_dump(exclude_none=True)
    assert "session_token" not in without_token

    # The field is optional so omitting it never breaks the model.
    field = (schema.data or {}).get("session_token")
    assert field is not None and field.field_type == Optional[str]


# --- Conversation token-secret helpers + end-to-end bind --------------------


async def test_conversation_token_secret_lifecycle(test_db):
    from jvagent.memory.conversation import Conversation

    conv = await Conversation.create(
        session_id="sess-tok-1", user_id="u1", channel="default"
    )
    try:
        assert conv.token_secret == ""  # empty until first mint
        secret = conv.ensure_token_secret()
        assert secret and conv.token_secret == secret
        assert conv.ensure_token_secret() == secret  # idempotent
        rotated = conv.rotate_token_secret()
        assert rotated != secret and conv.token_secret == rotated

        # A token minted against the rotated secret binds; the old one is revoked.
        tok = st.mint_session_token(
            agent_id="a1",
            session_id=conv.session_id,
            user_id=conv.user_id,
            token_secret=rotated,
        )
        claims, _ = st.verify_session_token(tok)
        assert st.claims_match_conversation(claims, conv) is None
        stale = st.mint_session_token(
            agent_id="a1",
            session_id=conv.session_id,
            user_id=conv.user_id,
            token_secret=secret,
        )
        stale_claims, _ = st.verify_session_token(stale)
        assert st.claims_match_conversation(stale_claims, conv) == "secret_mismatch"
    finally:
        await conv.delete(cascade=True)


async def test_issue_session_token_persists_secret(test_db):
    from jvagent.action.interact.endpoints import _issue_session_token
    from jvagent.memory.conversation import Conversation

    conv = await Conversation.create(
        session_id="sess-tok-2", user_id="u2", channel="default"
    )
    try:
        assert conv.token_secret == ""
        walker = SimpleNamespace(
            conversation=conv,
            session_id=conv.session_id,
            user_id=conv.user_id,
        )
        token = await _issue_session_token(walker, "a1")
        assert token
        assert conv.token_secret

        # Secret must survive a DB round-trip (not just in-memory mutation).
        reloaded = await Conversation.get(conv.id)
        assert reloaded is not None
        assert reloaded.token_secret == conv.token_secret
    finally:
        await conv.delete(cascade=True)
