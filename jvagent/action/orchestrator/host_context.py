"""Verification for authenticated, per-turn system context supplied by a host."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping, Optional


def verified_host_system_context(data: Any) -> Optional[str]:
    """Return Core-signed context only when its run binding and MAC are valid.

    ``visitor.data`` is normally caller-controlled. Never promote an arbitrary
    data field into the system prompt without this check.
    """
    if not isinstance(data, Mapping):
        return None
    envelope = data.get("host_system_context")
    run_id = str(data.get("run_id") or "")
    if not isinstance(envelope, Mapping) or not run_id:
        return None
    body = envelope.get("body")
    supplied = envelope.get("signature")
    if not isinstance(body, str) or len(body) > 65536 or not isinstance(supplied, str):
        return None
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(parsed, dict)
        or parsed.get("version") != 1
        or parsed.get("run_id") != run_id
        or not isinstance(parsed.get("context"), str)
    ):
        return None
    from jvagent.action.interact.session_token import _secret

    secret = _secret()
    if not secret:
        return None
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return None
    return parsed["context"]


def allows_empty_host_utterance(data: Any) -> bool:
    """Empty text is valid only when authenticated host context is present."""
    return bool(verified_host_system_context(data))
