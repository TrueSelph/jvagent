"""OAuth subsystem for Google/Microsoft and future providers."""

from jvagent.action.oauth.audit import _audit_log_oauth_event as audit_log_oauth_event
from jvagent.action.oauth.state import (
    DEFAULT_TTL_SECONDS,
    OAuthState,
    consume_oauth_state,
    create_oauth_state,
    prune_expired_oauth_states,
)
from jvagent.action.oauth.token_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_token_from_storage,
    encrypt_token_for_storage,
    encryption_available,
)

__all__ = [
    "CIPHER_PREFIX_V1",
    "DEFAULT_TTL_SECONDS",
    "OAuthState",
    "audit_log_oauth_event",
    "consume_oauth_state",
    "create_oauth_state",
    "decrypt_token_from_storage",
    "encrypt_token_for_storage",
    "encryption_available",
    "prune_expired_oauth_states",
]
