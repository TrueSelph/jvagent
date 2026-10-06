"""Verification and single-use handling for trusted host turn context."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any, Mapping, Optional

_ISSUER = "jvagent-host"
_AUDIENCE = "jvagent.interact.system-context"
_MAX_TTL_SECONDS = 300
_MAX_CONTEXT_BYTES = 65536
_MIN_SECRET_BYTES = 32
_MAX_IDENTIFIER_LENGTH = 128
_REPLAY_KEY = "_jvagent_host_context_nonces"
_MAX_REPLAY_ENTRIES = 512


def _secret() -> Optional[str]:
    """Return a sufficiently strong dedicated key, never the JWT key."""
    secret = os.getenv("JVAGENT_HOST_CONTEXT_SECRET", "").strip()
    return secret if len(secret.encode("utf-8")) >= _MIN_SECRET_BYTES else None


def sign_host_system_context(
    context: str,
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    run_id: Optional[str] = None,
    nonce: Optional[str] = None,
    ttl_seconds: int = 120,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Create the interoperable v2 request envelope for a trusted host.

    The host service must receive ``JVAGENT_HOST_CONTEXT_SECRET`` through its
    secret manager. Keep the generated envelope scoped to one request and never
    log or reuse it.
    """
    secret = _secret()
    if not secret:
        raise RuntimeError(
            "JVAGENT_HOST_CONTEXT_SECRET must be configured with at least "
            f"{_MIN_SECRET_BYTES} UTF-8 bytes"
        )
    if not isinstance(context, str) or not context.strip():
        raise ValueError("context must be a non-empty string")
    if not all(str(value or "").strip() for value in (agent_id, user_id, session_id)):
        raise ValueError("agent_id, user_id, and session_id are required")
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between 1 and {_MAX_TTL_SECONDS}")
    issued_at = int(time.time()) if now is None else int(now)
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        raise ValueError("run_id must be a non-empty string")
    if nonce is not None and (not isinstance(nonce, str) or not nonce.strip()):
        raise ValueError("nonce must be a non-empty string")
    request_id = run_id or uuid.uuid4().hex
    nonce_value = nonce or secrets.token_urlsafe(32)
    if (
        len(request_id) > _MAX_IDENTIFIER_LENGTH
        or len(nonce_value) > _MAX_IDENTIFIER_LENGTH
    ):
        raise ValueError(
            f"run_id and nonce must be at most {_MAX_IDENTIFIER_LENGTH} characters"
        )
    claims = {
        "version": 2,
        "issuer": _ISSUER,
        "audience": _AUDIENCE,
        "agent_id": str(agent_id),
        "user_id": str(user_id),
        "session_id": str(session_id),
        "run_id": request_id,
        "nonce": nonce_value,
        "issued_at": issued_at,
        "expires_at": issued_at + int(ttl_seconds),
        "context": context,
    }
    body = json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise ValueError("signed host context exceeds the 64 KiB envelope limit")
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "run_id": request_id,
        "host_system_context": {"body": body, "signature": signature},
    }


def verified_host_system_claims(
    data: Any,
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    now: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Verify a short-lived host envelope against the live caller identity.

    The signed ``run_id`` and ``nonce`` are request identifiers, not authority:
    authority comes from the signature and exact match to server-resolved caller
    fields. Version 1 envelopes are deliberately rejected.
    """
    if not isinstance(data, Mapping):
        return None
    envelope = data.get("host_system_context")
    run_id = data.get("run_id")
    if (
        not isinstance(envelope, Mapping)
        or not isinstance(run_id, str)
        or not run_id
        or len(run_id) > _MAX_IDENTIFIER_LENGTH
    ):
        return None
    body = envelope.get("body")
    supplied = envelope.get("signature")
    if (
        not isinstance(body, str)
        or len(body.encode("utf-8")) > _MAX_CONTEXT_BYTES
        or not isinstance(supplied, str)
    ):
        return None
    try:
        claims = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(claims, dict):
        return None
    secret = _secret()
    if not secret:
        return None
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return None

    timestamp = int(time.time()) if now is None else int(now)
    issued_at = claims.get("issued_at")
    expires_at = claims.get("expires_at")
    context = claims.get("context")
    nonce = claims.get("nonce")
    if (
        claims.get("version") != 2
        or claims.get("issuer") != _ISSUER
        or claims.get("audience") != _AUDIENCE
        or claims.get("agent_id") != str(agent_id or "")
        or claims.get("user_id") != str(user_id or "")
        or claims.get("session_id") != str(session_id or "")
        or claims.get("run_id") != run_id
        or type(issued_at) is not int
        or type(expires_at) is not int
        or issued_at > timestamp + 30
        or expires_at <= timestamp
        or expires_at <= issued_at
        or expires_at - issued_at > _MAX_TTL_SECONDS
        or not isinstance(nonce, str)
        or not nonce
        or len(nonce) > _MAX_IDENTIFIER_LENGTH
        or not isinstance(context, str)
        or not context.strip()
    ):
        return None
    return claims


def verified_host_system_context(
    data: Any,
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    now: Optional[int] = None,
) -> Optional[str]:
    """Return authenticated context only when it matches a live caller."""
    claims = verified_host_system_claims(
        data,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        now=now,
    )
    return str(claims["context"]) if claims else None


async def consume_host_system_context(visitor: Any) -> Optional[str]:
    """Validate and durably record a nonce before promoting host context.

    The ledger lives in the existing graph conversation, so replay state follows
    the same persistence owner as the session rather than a process cache. Reload
    the node *inside* the conversation mutation lock: callers may have resolved
    stale Conversation snapshots before they contend for that lock. If locking,
    storage, or the bounded ledger fails, reject the context.
    """
    conversation = getattr(visitor, "conversation", None)
    claims = verified_host_system_claims(
        getattr(visitor, "data", None),
        agent_id=str(getattr(visitor, "agent_id", "") or ""),
        user_id=str(getattr(visitor, "user_id", "") or ""),
        session_id=str(getattr(visitor, "session_id", "") or ""),
    )
    conversation_id = str(getattr(conversation, "id", "") or "")
    if claims is None or not conversation_id:
        return None
    try:
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.distributed_conversation_lock import (
            conversation_mutation_lock,
        )

        async with conversation_mutation_lock(conversation_id):
            current = await Conversation.get(conversation_id)
            if current is None:
                return None
            conversation_context = getattr(current, "context", None)
            if not isinstance(conversation_context, dict):
                return None
            ledger = conversation_context.get(_REPLAY_KEY, {})
            if not isinstance(ledger, dict):
                return None
            timestamp = int(time.time())
            ledger = {
                key: entry
                for key, entry in ledger.items()
                if (
                    isinstance(key, str)
                    and isinstance(entry, dict)
                    and isinstance(entry.get("expires_at"), int)
                    and entry["expires_at"] > timestamp
                )
            }
            nonce = str(claims["nonce"])
            correlation_id = str(getattr(visitor, "correlation_id", "") or "")
            if (
                not correlation_id
                or nonce in ledger
                or len(ledger) >= _MAX_REPLAY_ENTRIES
            ):
                return None
            ledger[nonce] = {
                "expires_at": int(claims["expires_at"]),
                "correlation_id": correlation_id,
                "run_id": str(claims["run_id"]),
            }
            conversation_context[_REPLAY_KEY] = ledger
            await current.save()
            # Keep later walker work on the freshly loaded node that now carries
            # the persisted nonce ledger, rather than its stale bootstrap object.
            visitor.conversation = current
    except Exception:
        return None
    return str(claims["context"])


def allows_empty_host_utterance(
    data: Any, *, agent_id: str, user_id: str, session_id: str
) -> bool:
    """Empty text is valid only for an unexpired caller-bound host envelope."""
    return bool(
        verified_host_system_context(
            data,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )
    )
