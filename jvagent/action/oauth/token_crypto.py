"""AES-256-GCM encryption for OAuth access + refresh tokens at rest.

AUDIT-actions XC-1 Fix 2.

Tokens persisted on ``GoogleToken`` / ``MicrosoftToken`` graph nodes are
ciphered with a deployment-scoped key sourced from
``JVAGENT_TOKEN_ENC_KEY`` (preferred) or, as a fallback, derived from
``JVSPATIAL_JWT_SECRET_KEY`` via HKDF when the dedicated key is unset.

Ciphertext format (string column-friendly)::

    v1:<base64url(nonce ‖ ciphertext ‖ tag)>

The ``v1:`` version tag lets later migration to a new cipher coexist
with legacy rows. Reads also accept:

- Strings WITHOUT a recognized version prefix → treated as plaintext
  (legacy rows from before this fix landed). They become ciphertext on
  the next save via :func:`encrypt_token_for_storage`.
- Empty strings → returned unchanged (no-op).

Key rotation is supported via ``JVAGENT_TOKEN_ENC_KEY_PREVIOUS``. On
decrypt, current key is tried first, then previous. Rotation cycle:
deploy current+previous; let next-save re-encrypt all rows with current;
drop previous from env once observation confirms full coverage.

NEVER pass tokens into logs. The encrypt/decrypt errors here are logged
WITHOUT including ciphertext or plaintext bodies.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from typing import Callable, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

CIPHER_PREFIX_V1 = "v1:"
_NONCE_BYTES = 12  # AES-GCM canonical nonce length.
_HKDF_INFO = b"jvagent-oauth-token-encryption-v1"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _derive_key_from_jwt(jwt_secret: str) -> bytes:
    """HKDF-Extract-and-Expand JWT secret to 32 bytes for AES-256.

    Falls back to a manual HKDF using HMAC-SHA256 so we don't pull in
    cryptography.hazmat.primitives.kdf.hkdf (some build envs lag).
    """
    # HKDF-Extract: PRK = HMAC-SHA256(salt=b"jvagent-token-enc", ikm=jwt_secret)
    h = hmac.HMAC(b"jvagent-token-enc", hashes.SHA256())
    h.update(jwt_secret.encode("utf-8"))
    prk = h.finalize()
    # HKDF-Expand: T(1) = HMAC-SHA256(PRK, info || 0x01)
    e = hmac.HMAC(prk, hashes.SHA256())
    e.update(_HKDF_INFO + b"\x01")
    return e.finalize()  # 32 bytes — perfect for AES-256.


def _normalize_key_bytes(raw: str) -> Optional[bytes]:
    """Return 32 bytes from an env value or None if unusable.

    Accepted forms:
    - base64 (urlsafe or std) of 32 raw bytes
    - hex of 32 raw bytes
    - raw 32-char string
    """
    if not raw:
        return None
    raw = raw.strip()
    # Try base64 urlsafe first.
    decoders: list[Callable[[str], bytes]] = [_b64url_decode, base64.b64decode]
    for decoder in decoders:
        try:
            candidate = decoder(raw)
            if len(candidate) == 32:
                return candidate
        except Exception:
            pass
    # Try hex.
    try:
        candidate = bytes.fromhex(raw)
        if len(candidate) == 32:
            return candidate
    except Exception:
        pass
    # Raw bytes.
    encoded = raw.encode("utf-8")
    if len(encoded) == 32:
        return encoded
    return None


def _current_key() -> Optional[bytes]:
    """Resolve the active encryption key. Returns None if unconfigured.

    Order:
    1. JVAGENT_TOKEN_ENC_KEY (explicit, recommended)
    2. JVSPATIAL_JWT_SECRET_KEY (fallback, HKDF-derived to 32 bytes)
    """
    explicit = os.environ.get("JVAGENT_TOKEN_ENC_KEY", "")
    if explicit:
        key = _normalize_key_bytes(explicit)
        if key is None:
            logger.warning(
                "JVAGENT_TOKEN_ENC_KEY set but did not yield 32 bytes; "
                "tokens will fall back to plaintext storage."
            )
            return None
        return key
    jwt = os.environ.get("JVSPATIAL_JWT_SECRET_KEY", "")
    if jwt:
        return _derive_key_from_jwt(jwt)
    return None


def _previous_key() -> Optional[bytes]:
    """Resolve the previous-rotation key for read-only decrypt."""
    prev = os.environ.get("JVAGENT_TOKEN_ENC_KEY_PREVIOUS", "")
    if not prev:
        return None
    return _normalize_key_bytes(prev)


def encryption_available() -> bool:
    """True iff a usable encryption key is configured.

    Callers can fall back to plaintext storage when this is False — used
    by the migration window so old deployments without the env var still
    work, just without at-rest encryption.
    """
    return _current_key() is not None


def encrypt_token_for_storage(plaintext: str) -> str:
    """Encrypt ``plaintext`` with the current key, returning the
    ``v1:<b64url(nonce||ciphertext||tag)>`` envelope.

    No key configured → returns the plaintext unchanged. Empty
    ``plaintext`` → returns it unchanged.
    """
    if not plaintext:
        return plaintext
    key = _current_key()
    if key is None:
        return plaintext
    nonce = secrets.token_bytes(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return CIPHER_PREFIX_V1 + _b64url_encode(nonce + ct_with_tag)


def decrypt_token_from_storage(stored: str) -> str:
    """Decrypt a stored value. Accepts ciphertext OR legacy plaintext.

    Returns the empty string on decryption failure (logs at WARNING
    without the ciphertext body). Returns the input unchanged when:
    - Empty string.
    - String without the ``v1:`` prefix (legacy plaintext row).
    """
    if not stored:
        return stored
    if not stored.startswith(CIPHER_PREFIX_V1):
        # Legacy plaintext row — returned as-is. Next save encrypts.
        return stored

    body = stored[len(CIPHER_PREFIX_V1) :]
    try:
        blob = _b64url_decode(body)
    except Exception:
        logger.warning(
            "decrypt_token_from_storage: malformed base64 payload; "
            "returning empty so caller can re-auth"
        )
        return ""
    if len(blob) <= _NONCE_BYTES:
        logger.warning("decrypt_token_from_storage: payload too short; returning empty")
        return ""
    nonce, ct_with_tag = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]

    for key, label in (
        (_current_key(), "current"),
        (_previous_key(), "previous"),
    ):
        if key is None:
            continue
        try:
            plaintext = AESGCM(key).decrypt(nonce, ct_with_tag, associated_data=None)
            return plaintext.decode("utf-8")
        except InvalidTag:
            continue
        except Exception as exc:
            logger.warning(
                "decrypt_token_from_storage: %s key failed with %s",
                label,
                type(exc).__name__,
            )
            continue

    logger.warning(
        "decrypt_token_from_storage: no key decrypted the payload; "
        "rotate JVAGENT_TOKEN_ENC_KEY_PREVIOUS or have the user re-auth"
    )
    return ""
