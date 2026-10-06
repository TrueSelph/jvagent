import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.host_context import (
    allows_empty_host_utterance,
    consume_host_system_context,
    sign_host_system_context,
    verified_host_system_context,
)

CALLER = {"agent_id": "agent-1", "user_id": "user-1", "session_id": "session-1"}
TEST_SECRET = "unit-test-host-context-key-with-32-bytes-minimum"


def _patch_conversation_loader(monkeypatch, loader):
    from jvagent.memory.conversation import Conversation

    async def _get(_cls, _conversation_id):
        return loader()

    async def _find_one(_cls, query=None, **kwargs):
        return loader()

    monkeypatch.setattr(Conversation, "get", classmethod(_get))
    monkeypatch.setattr(Conversation, "find_one", classmethod(_find_one))


def _envelope(
    *,
    context="host policy",
    run_id="run-1",
    nonce="nonce-1",
    now=1000,
    secret=TEST_SECRET,
):
    claims = {
        "version": 2,
        "issuer": "jvagent-host",
        "audience": "jvagent.interact.system-context",
        **CALLER,
        "run_id": run_id,
        "nonce": nonce,
        "issued_at": now,
        "expires_at": now + 120,
        "context": context,
    }
    body = json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {
        "run_id": run_id,
        "host_system_context": {"body": body, "signature": signature},
    }


def _verify(data, **overrides):
    return verified_host_system_context(data, **(CALLER | overrides), now=1001)


def test_host_context_is_bound_to_live_caller_and_expiry(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    data = _envelope()

    assert _verify(data) == "host policy"
    assert allows_empty_host_utterance(data, **CALLER)
    assert _verify(data, user_id="another-user") is None
    assert _verify(data, session_id="another-session") is None
    assert _verify(data, agent_id="another-agent") is None

    assert _verify(_envelope(now=700)) is None
    assert _verify(_envelope(now=1100)) is None


def test_host_context_signer_emits_a_verifiable_single_request_envelope(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1000)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    data = sign_host_system_context(
        "trusted host context",
        **CALLER,
        run_id="run-signed",
        nonce="fresh-nonce",
        ttl_seconds=90,
    )
    assert data["run_id"] == "run-signed"
    assert (
        verified_host_system_context(data, **CALLER, now=1001) == "trusted host context"
    )
    assert verified_host_system_context(data, **CALLER, now=1090) is None


def test_signer_requires_strong_key_and_exact_integer_ttl(monkeypatch):
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", "too-short")
    from jvagent.action.orchestrator import host_context

    with pytest.raises(RuntimeError, match="at least 32 UTF-8 bytes"):
        sign_host_system_context("policy", **CALLER)

    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    for ttl_seconds in (True, 1.5, "90"):
        with pytest.raises(ValueError, match="ttl_seconds"):
            sign_host_system_context(
                "policy", **CALLER, ttl_seconds=ttl_seconds  # type: ignore[arg-type]
            )

    assert host_context._secret() == TEST_SECRET


def test_host_context_rejects_tampering_legacy_secret_and_bad_claims(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    envelope = _envelope()
    envelope["host_system_context"]["body"] = envelope["host_system_context"][
        "body"
    ].replace("host policy", "forged policy")
    assert _verify(envelope) is None

    monkeypatch.delenv("JVAGENT_HOST_CONTEXT_SECRET")
    assert _verify(_envelope()) is None
    assert (
        _verify(_envelope(secret="jwt-secret-that-is-not-the-dedicated-host-key"))
        is None
    )


def test_host_context_rejects_boolean_timestamps_and_oversized_run_ids(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    data = _envelope()
    claims = json.loads(data["host_system_context"]["body"])
    claims["issued_at"] = True
    body = json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(
        TEST_SECRET.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    data["host_system_context"] = {"body": body, "signature": signature}
    assert _verify(data) is None

    oversized = _envelope(run_id="r" * (host_context._MAX_IDENTIFIER_LENGTH + 1))
    assert _verify(oversized) is None


async def test_consumed_host_context_nonce_cannot_be_replayed(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)

    class Conversation:
        id = "conversation-1"

        def __init__(self):
            self.context = {}
            self.save_count = 0

        async def save(self):
            self.save_count += 1
            return None

    conversation = Conversation()
    _patch_conversation_loader(monkeypatch, lambda: conversation)
    visitor = SimpleNamespace(
        data=_envelope(),
        conversation=conversation,
        correlation_id="corr-1",
        **CALLER,
    )
    assert await consume_host_system_context(visitor) == "host policy"
    assert visitor.conversation.save_count == 1
    ledger = visitor.conversation.context[host_context._REPLAY_KEY]
    assert ledger["nonce-1"]["correlation_id"] == "corr-1"
    assert ledger["nonce-1"]["run_id"] == "run-1"
    assert await consume_host_system_context(visitor) is None
    assert visitor.conversation.save_count == 1


async def test_replay_ledger_failure_and_capacity_fail_closed(monkeypatch):
    from jvagent.action.orchestrator import host_context

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)

    class BrokenConversation:
        id = "conversation-1"

        def __init__(self):
            self.context = {}

        async def save(self):
            raise RuntimeError("write failed")

    broken = BrokenConversation()
    _patch_conversation_loader(monkeypatch, lambda: broken)
    visitor = SimpleNamespace(
        data=_envelope(),
        conversation=broken,
        correlation_id="corr-1",
        **CALLER,
    )
    assert await consume_host_system_context(visitor) is None

    full = {
        f"n-{i}": {"expires_at": 2000, "correlation_id": "corr", "run_id": "run"}
        for i in range(host_context._MAX_REPLAY_ENTRIES)
    }

    class FullConversation:
        id = "conversation-1"

        def __init__(self):
            self.context = {host_context._REPLAY_KEY: full}

        async def save(self):
            raise AssertionError("must reject before persisting")

    full_conversation = FullConversation()
    _patch_conversation_loader(monkeypatch, lambda: full_conversation)
    visitor.conversation = full_conversation
    assert await consume_host_system_context(visitor) is None


async def test_concurrent_context_replay_reloads_conversation_inside_lock(monkeypatch):
    from jvagent.action.orchestrator import host_context
    from jvagent.memory.conversation import Conversation

    monkeypatch.setattr(host_context.time, "time", lambda: 1001)
    monkeypatch.setenv("JVAGENT_HOST_CONTEXT_SECRET", TEST_SECRET)
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", raising=False)
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", raising=False)

    conversation = await Conversation.create(
        session_id="host-context-replay-session",
        user_id=CALLER["user_id"],
        channel="default",
    )
    # Resolve both nodes before either request acquires the mutation lock. This
    # reproduces the stale-bootstrap-snapshot race against the actual JSON graph.
    stale_snapshots = [
        await Conversation.get(conversation.id),
        await Conversation.get(conversation.id),
    ]
    assert all(snapshot is not None for snapshot in stale_snapshots)
    envelope = _envelope(nonce="single-use")
    visitors = [
        SimpleNamespace(
            data=envelope,
            conversation=snapshot,
            correlation_id=f"corr-{index}",
            **CALLER,
        )
        for index, snapshot in enumerate(stale_snapshots)
    ]

    results = await asyncio.gather(
        *(consume_host_system_context(visitor) for visitor in visitors)
    )

    assert sorted(result is not None for result in results) == [False, True]
    persisted = await Conversation.get(conversation.id)
    assert persisted is not None
    assert persisted.context[host_context._REPLAY_KEY]["single-use"][
        "correlation_id"
    ] in {"corr-0", "corr-1"}
