"""Session capability tokens for the public ``interact`` endpoint (ADR-0020).

The public ``POST /agents/{id}/interact`` endpoint is intentionally
``auth=False`` so it can serve anonymous, embeddable chat. Historically both
``user_id`` and ``session_id`` were **client-asserted strings the server never
issued and could not verify** — making ``session_id`` a forgeable bearer
credential (hijack / IDOR / enumeration oracle). This module restores integrity
with two doors:

* **Mode A** — a real jvspatial login JWT (``Authorization: Bearer``): trust
  ``user_id`` from the verified token (jvchat / embed hosts).
* **Mode B** — an anonymous **session capability token** minted by the server on
  first contact and required on every resume. It is a short-lived HS256 JWT,
  signed with the same ``JVSPATIAL_JWT_SECRET_KEY`` jvspatial's auth uses, whose
  claims bind it to one ``Conversation`` (``agent_id``/``session_id``/``user_id``
  + a per-conversation ``cs`` secret) and scope it to the web channel.

No bespoke crypto: we reuse jvspatial's HS256 signer + key. Revocation is free —
rotating ``Conversation.token_secret`` invalidates outstanding tokens.

Staged rollout via ``JVAGENT_INTERACT_PUBLIC_AUTH ∈ {off, log, required}``:

* ``off``    — preserve legacy behavior (embed hosts that own identity).
* ``log``    — mint + verify, but never reject; log what *would* be denied.
* ``required`` — enforce.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import jwt
from jvspatial.env import env

logger = logging.getLogger(__name__)

# Canonical scope for web-minted tokens. The interact endpoint normalizes the
# incoming "web"/"" channel to "default", so a conversation is "web-owned" when
# its channel is one of these. Channel-owned sessions (whatsapp, messenger,
# email, …) are intentionally NOT reachable through a web session token.
WEB_CHANNELS = frozenset({"default", "web", ""})
_TOKEN_CHANNEL = "web"

_ALGORITHM = "HS256"
_DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

# Staged-rollout modes.
MODE_OFF = "off"
MODE_LOG = "log"
MODE_REQUIRED = "required"
_VALID_MODES = (MODE_OFF, MODE_LOG, MODE_REQUIRED)


def auth_mode() -> str:
    """Resolve ``JVAGENT_INTERACT_PUBLIC_AUTH``.

    Defaults to ``off`` in development and ``required`` in production when
    unset, so internet-facing deploys fail closed without an explicit opt-out.
    Unknown values fall back to ``off`` (fail-open) so a misconfiguration never
    locks out the public endpoint — enforcement is opt-in via explicit values.
    """
    from jvagent.core.config import is_production_mode

    raw = env("JVAGENT_INTERACT_PUBLIC_AUTH", default=None)
    if raw is None or not str(raw).strip():
        return MODE_REQUIRED if is_production_mode() else MODE_OFF
    raw = str(raw).strip().lower()
    return raw if raw in _VALID_MODES else MODE_OFF


def token_ttl_seconds() -> int:
    """Session-token lifetime in seconds (``JVAGENT_INTERACT_TOKEN_TTL_SECONDS``)."""
    try:
        val = int(
            env("JVAGENT_INTERACT_TOKEN_TTL_SECONDS", default=_DEFAULT_TTL_SECONDS)
        )
        return val if val > 0 else _DEFAULT_TTL_SECONDS
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SECONDS


def _secret() -> Optional[str]:
    """Shared HS256 secret (``JVSPATIAL_JWT_SECRET_KEY``) — None if unset.

    Reuses jvspatial's auth secret so Mode A login tokens verify with the same
    key and Mode B tokens share one signing mechanism (ADR-0020 §2).
    """
    val = env("JVSPATIAL_JWT_SECRET_KEY", default=None)
    return val or None


def is_web_channel(channel: Optional[str]) -> bool:
    """True when ``channel`` is a web-owned channel (vs. a provider channel)."""
    return (channel or "").strip().lower() in WEB_CHANNELS


def mint_session_token(
    *,
    agent_id: str,
    session_id: str,
    user_id: str,
    token_secret: str,
    ttl_seconds: Optional[int] = None,
) -> Optional[str]:
    """Mint a web-scoped anonymous session capability token (Mode B).

    Returns ``None`` when no signing secret is configured (so the caller can
    degrade gracefully in ``off``/``log`` modes rather than raise).
    """
    secret = _secret()
    if not secret:
        return None
    now = int(time.time())
    ttl = ttl_seconds if (ttl_seconds and ttl_seconds > 0) else token_ttl_seconds()
    payload = {
        "agent_id": agent_id,
        "session_id": session_id,
        "user_id": user_id,
        "channel": _TOKEN_CHANNEL,
        "cs": token_secret,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "typ": "interact_session",
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_session_token(
    token: str, *, expected_agent_id: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify a Mode B session token's signature + expiry (not its conversation).

    Returns ``(claims, None)`` on success or ``(None, reason)`` on failure.
    Binding the claims to a loaded ``Conversation`` is a separate step —
    :func:`claims_match_conversation` — because the conversation is resolved
    later in the request.
    """
    secret = _secret()
    if not secret:
        return None, "no_secret_configured"
    if not token:
        return None, "missing_token"
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None, "expired"
    except jwt.InvalidTokenError as exc:
        return None, f"invalid:{type(exc).__name__}"
    if claims.get("typ") != "interact_session":
        return None, "wrong_type"
    if expected_agent_id and claims.get("agent_id") != expected_agent_id:
        return None, "agent_mismatch"
    return claims, None


def verify_bearer(token: str) -> Optional[str]:
    """Verify a Mode A jvspatial login JWT; return its ``user_id`` or ``None``.

    Decodes with the shared HS256 secret (the same key jvspatial signs login
    tokens with) and reads the identity claim — jvspatial uses ``user_id``;
    ``sub`` is accepted as a fallback. Signature/expiry are enforced by
    ``jwt.decode``. Blacklist/jti revocation is owned by jvspatial's auth
    surface and is out of scope for this public-ingress guard.
    """
    secret = _secret()
    if not secret or not token:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    # A session token is not a login bearer; don't let it satisfy Mode A.
    if claims.get("typ") == "interact_session":
        return None
    uid = claims.get("user_id") or claims.get("sub")
    return str(uid) if uid else None


def claims_match_conversation(
    claims: Dict[str, Any], conversation: Any
) -> Optional[str]:
    """Check verified token claims against a loaded ``Conversation``.

    Returns ``None`` when the token legitimately authorizes this conversation,
    else a short machine reason. Enforces: matching ``session_id``/``user_id``,
    ``cs == Conversation.token_secret`` (per-conversation revocable secret), and
    that the conversation is web-owned (a web token cannot resume a
    provider-channel session — ADR-0020 §2.3).
    """
    if conversation is None:
        return "no_conversation"
    if not is_web_channel(getattr(conversation, "channel", None)):
        return "cross_channel"
    if claims.get("session_id") != getattr(conversation, "session_id", None):
        return "session_mismatch"
    if claims.get("user_id") != getattr(conversation, "user_id", None):
        return "user_mismatch"
    secret = getattr(conversation, "token_secret", "") or ""
    if not secret:
        # Pre-existing conversation with no secret yet: backfill is the caller's
        # job (lazy on resume). Treat as a soft miss so `log` mode can observe it
        # and `required` mode can reject until backfilled.
        return "no_token_secret"
    if not hmac.compare_digest(str(claims.get("cs") or ""), secret):
        return "secret_mismatch"
    return None


@dataclass
class IdentityDecision:
    """Outcome of the pre-spawn identity guard for one interact call.

    ``verified_user_id`` is threaded into the walker (overriding any
    client-asserted ``user_id``) when identity was proven; ``None`` means "use
    the client value" (create paths / ``off`` mode). ``denial`` records that the
    call *would* be rejected; ``reject`` is the effective decision — true only in
    ``required`` mode — so ``log`` mode observes without breaking anyone.
    """

    mode: str
    verified_user_id: Optional[str]
    via: str  # "bearer" | "session_token" | "none"
    denial: bool = False
    reason: str = "ok"

    @property
    def reject(self) -> bool:
        return self.denial and self.mode == MODE_REQUIRED


def _extract_bearer(request: Any) -> Optional[str]:
    try:
        header = request.headers.get("authorization") or ""
    except Exception:  # pragma: no cover - defensive
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def _extract_session_token(request: Any) -> Optional[str]:
    try:
        tok = request.headers.get("x-session-token")
    except Exception:  # pragma: no cover - defensive
        return None
    return tok.strip() if tok else None


async def _load_conversation(agent: Any, session_id: str) -> Optional[Any]:
    """Best-effort load of an agent-owned Conversation by session id (or None)."""
    try:
        memory = await agent.get_memory()
        if memory is None:
            return None
        return await memory.get_conversation_by_session(session_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("session_token: conversation preload failed: %s", exc)
        return None


async def resolve_interact_identity(
    *,
    request: Any,
    agent: Any,
    agent_id: str,
    session_id: Optional[str],
    user_id: Optional[str],
) -> IdentityDecision:
    """Resolve the caller's verified identity for a public interact call.

    Two doors (ADR-0020 §2): a jvspatial login JWT (Mode A) or an anonymous
    session capability token (Mode B). Returns an :class:`IdentityDecision`; the
    endpoint enforces it (reject in ``required``, observe in ``log``, ignore in
    ``off``) and mints/refreshes the Mode B token post-spawn.
    """
    mode = auth_mode()
    if mode == MODE_OFF:
        return IdentityDecision(mode=mode, verified_user_id=None, via="none")

    bearer = _extract_bearer(request)
    stoken = _extract_session_token(request)

    # --- Mode A: authenticated login JWT ---
    if bearer:
        uid = verify_bearer(bearer)
        if uid:
            # If resuming an existing conversation, it must belong to this user.
            if session_id:
                conv = await _load_conversation(agent, session_id)
                if conv is not None and getattr(conv, "user_id", None) not in (
                    None,
                    "",
                    uid,
                ):
                    return IdentityDecision(
                        mode=mode,
                        verified_user_id=uid,
                        via="bearer",
                        denial=True,
                        reason="bearer_not_owner",
                    )
            return IdentityDecision(mode=mode, verified_user_id=uid, via="bearer")
        # Bearer present but invalid → a denial signal (fall through in log mode).
        return IdentityDecision(
            mode=mode,
            verified_user_id=None,
            via="none",
            denial=True,
            reason="invalid_bearer",
        )

    # --- Mode B: anonymous session capability token ---
    if not session_id:
        # Create path: no credential required; a token is minted post-spawn.
        return IdentityDecision(
            mode=mode, verified_user_id=None, via="none", reason="create"
        )

    conv = await _load_conversation(agent, session_id)
    if conv is None:
        # No existing agent-owned conversation for this id → a pinned-id create,
        # not a resume; allow (the walker still guards foreign sessions).
        return IdentityDecision(
            mode=mode, verified_user_id=None, via="none", reason="resume_new"
        )

    # An existing conversation is being resumed → a token is required.
    if not stoken:
        return IdentityDecision(
            mode=mode,
            verified_user_id=None,
            via="none",
            denial=True,
            reason="missing_session_token",
        )
    claims, err = verify_session_token(stoken, expected_agent_id=agent_id)
    if err or claims is None:
        return IdentityDecision(
            mode=mode,
            verified_user_id=None,
            via="none",
            denial=True,
            reason=f"token_{err or 'invalid'}",
        )
    bind_err = claims_match_conversation(claims, conv)
    if bind_err:
        return IdentityDecision(
            mode=mode,
            verified_user_id=None,
            via="none",
            denial=True,
            reason=f"bind_{bind_err}",
        )
    return IdentityDecision(
        mode=mode,
        verified_user_id=str(claims.get("user_id") or ""),
        via="session_token",
        reason="ok",
    )


def warn_interact_auth_configuration() -> None:
    """Log production safety warnings for public interact authentication."""
    mode = auth_mode()
    secret = _secret()
    from jvagent.core.config import is_production_mode

    if not is_production_mode():
        return
    if mode == MODE_OFF:
        logger.warning(
            "PRODUCTION SAFETY: JVAGENT_INTERACT_PUBLIC_AUTH=off — the public "
            "interact endpoint accepts unauthenticated session resumes. Set "
            "JVAGENT_INTERACT_PUBLIC_AUTH=required and JVSPATIAL_JWT_SECRET_KEY."
        )
    elif mode in (MODE_LOG, MODE_REQUIRED) and not secret:
        logger.warning(
            "PRODUCTION SAFETY: interact session tokens require "
            "JVSPATIAL_JWT_SECRET_KEY when JVAGENT_INTERACT_PUBLIC_AUTH=%s.",
            mode,
        )


__all__ = [
    "MODE_OFF",
    "MODE_LOG",
    "MODE_REQUIRED",
    "WEB_CHANNELS",
    "IdentityDecision",
    "auth_mode",
    "token_ttl_seconds",
    "is_web_channel",
    "mint_session_token",
    "verify_session_token",
    "verify_bearer",
    "claims_match_conversation",
    "resolve_interact_identity",
    "warn_interact_auth_configuration",
]
