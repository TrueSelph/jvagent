"""Structured audit logger for OAuth lifecycle events.

AUDIT-actions XC-1 Fix 3.

Routes every mint / refresh / revoke / failure for Google + Microsoft
through a single audit channel. Each call emits at INFO level on the
``jvagent.action.oauth.audit`` logger with structured ``extra`` fields:

    event:         token_saved | token_refresh_failed | token_revoked
    provider:      google | microsoft
    action_id:     ID of the owning Action node
    agent_id:      Owning agent
    client_id:     Last 8 chars of the client_id (truncated to avoid full leak)
    timestamp:     ISO8601 UTC

Operators wire this to a security log bucket separately (see
``docs/observability.md``); the standard ``configure_standard_logging``
already preserves DBLogHandler if installed, so audit events flow into
the ``logs`` database alongside other events.

This module does NOT log tokens, refresh tokens, or client secrets.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

_AUDIT_LOGGER_NAME = "jvagent.action.oauth.audit"
_audit_logger = logging.getLogger(_AUDIT_LOGGER_NAME)


def _truncate_for_logs(value: Optional[str], keep: int = 8) -> str:
    """Return the last ``keep`` chars of ``value`` for log identification,
    with the remainder masked. Empty / None → empty string."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= keep:
        return s
    return f"…{s[-keep:]}"


def _audit_log_oauth_event(
    *,
    provider: str,
    event: str,
    action_id: str,
    agent_id: str,
    client_id_hint: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> None:
    """Emit a structured audit log line for an OAuth lifecycle event.

    Args:
        provider: ``"google"`` or ``"microsoft"`` (other providers if
            added later).
        event: Short event name (``token_saved`` / ``token_refresh_failed``
            / ``token_revoked`` / ``token_load_failed``).
        action_id: ID of the owning Action node.
        agent_id: Owning agent.
        client_id_hint: Truncated client_id for correlation. NEVER pass
            the access_token, refresh_token, or client_secret here.
        extra_details: Optional dict of additional structured fields
            (e.g. ``{"reason": "expired"}``). MUST NOT contain secrets.
    """
    details: dict = {
        "provider": provider,
        "event": event,
        "action_id": action_id,
        "agent_id": agent_id,
        "client_id_tail": _truncate_for_logs(client_id_hint),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra_details:
        # Best-effort: drop any keys that smell like secrets so a caller
        # mistake can't leak tokens into the audit channel.
        for k, v in extra_details.items():
            if any(
                bad in k.lower()
                for bad in (
                    "token",
                    "secret",
                    "password",
                    "key",
                    "authorization",
                )
            ):
                continue
            details[k] = v
    _audit_logger.info("oauth_event", extra={"details": details})
