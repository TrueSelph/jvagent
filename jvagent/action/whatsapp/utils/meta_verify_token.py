"""Deterministic Meta webhook verify token derivation."""

import hashlib
import hmac


def derive_meta_verify_token(agent_id: str, app_secret: str) -> str:
    """Return a stable verify token for Meta hub.challenge and Graph override.

    Derived from agent id + app secret so no env/yaml verify_token is required.
    Changes when ``--purge`` creates a new agent node id (override re-registers on startup).
    """
    aid = (agent_id or "").strip()
    secret = (app_secret or "").strip()
    if not aid or not secret:
        return ""
    digest = hmac.new(
        secret.encode("utf-8"),
        f"jvagent-meta-verify:{aid}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]
