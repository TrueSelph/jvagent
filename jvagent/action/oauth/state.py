"""Server-side OAuth ``state`` store with one-shot consumption.

The OAuth 2.0 ``state`` parameter is meant to be an unguessable, single-use
CSRF token: the authorization server echoes it back unchanged so the client
can verify the callback corresponds to a request *it* initiated. The PKCE
``code_verifier`` is a separate secret that travels only between client and
authorization-token endpoint at exchange time — never through the browser or
the IdP.

Previous implementations of :class:`GoogleAction` / :class:`MicrosoftAction`
packed ``f"{action_id}:{code_verifier}"`` into ``state`` and used the
client-supplied parts at callback time. That:

1. Leaks the PKCE verifier to the user's address bar / browser history /
   proxy access logs / IdP.
2. Removes anti-CSRF protection at the callback (the action_id is well
   known, so the state cannot be unguessed by the server).
3. Provides no replay defense for the verifier.

This module fixes both by:

- Generating ``state`` as :func:`secrets.token_urlsafe(32)`.
- Persisting ``(state, action_id, provider, code_verifier, redirect_uri,
  expires_at)`` server-side as an :class:`OAuthState` node.
- Looking up + **deleting** the row on callback (one-shot).
- Rejecting expired / wrong-provider entries.

AUDIT-actions XC-2.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from pydantic import field_validator

DEFAULT_TTL_SECONDS = 600  # 10 minutes
STATE_TOKEN_BYTES = 32


class OAuthState(Node):
    """One-shot OAuth state record. Created at authorize-URL build time,
    consumed (and deleted) at callback time.

    Indexed on ``state_token`` for O(1) lookup. The node is short-lived;
    expired rows are pruned by :func:`consume_oauth_state` (one-shot delete)
    and by :func:`prune_expired_oauth_states` (periodic sweep, optional).
    """

    state_token: str = attribute(
        indexed=True,
        index_unique=True,
        default_factory=str,
        description="Opaque CSRF state token (secrets.token_urlsafe).",
    )
    action_id: str = attribute(
        indexed=True,
        default_factory=str,
        description="ID of the Action that initiated the OAuth flow.",
    )
    provider: str = attribute(
        default_factory=str,
        description="Provider identifier (e.g. 'google', 'microsoft').",
    )
    code_verifier: str = attribute(
        default_factory=str,
        description="PKCE code_verifier; stays server-side, never sent to IdP.",
    )
    redirect_uri: str = attribute(
        default_factory=str,
        description="The redirect_uri sent to the IdP for this flow.",
    )
    expires_at: Optional[datetime] = attribute(
        default=None,
        description="UTC timestamp after which this state is invalid.",
    )

    @field_validator("expires_at", mode="before")
    @classmethod
    def _coerce_expires_at(cls, v: Any) -> Optional[datetime]:
        if v is None or v == "":
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, str):
            parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return v


async def create_oauth_state(
    *,
    action_id: str,
    provider: str,
    code_verifier: str,
    redirect_uri: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Persist a fresh OAuth state row and return the opaque state token.

    The returned string is what gets passed as ``state=`` to the IdP. It does
    NOT contain the action_id or code_verifier — those live only in the DB.
    """
    token = secrets.token_urlsafe(STATE_TOKEN_BYTES)
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await OAuthState.create(
        state_token=token,
        action_id=action_id,
        provider=provider,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        expires_at=expires,
    )
    return token


async def consume_oauth_state(
    state_token: str, *, provider: str
) -> Optional[OAuthState]:
    """One-shot lookup. Returns the row and deletes it; ``None`` otherwise.

    Rejects rows whose ``provider`` does not match the caller's provider, and
    rows that have expired. Always deletes the matched row (even if rejected)
    so a leaked or replayed state cannot be retried.
    """
    if not state_token:
        return None
    rows = await OAuthState.find({"context.state_token": state_token})
    if not rows:
        return None
    row = rows[0]
    try:
        await row.delete()
    except Exception:
        pass
    if row.provider != provider:
        return None
    if row.expires_at is None or row.expires_at < datetime.now(timezone.utc):
        return None
    return row


async def prune_expired_oauth_states() -> int:
    """Delete all expired OAuthState rows. Returns the count removed.

    Safe to call from a periodic maintenance hook. Idempotent.
    """
    removed = 0
    now = datetime.now(timezone.utc)
    rows = await OAuthState.find({})
    for row in rows:
        if row.expires_at is None or row.expires_at < now:
            try:
                await row.delete()
                removed += 1
            except Exception:
                continue
    return removed
