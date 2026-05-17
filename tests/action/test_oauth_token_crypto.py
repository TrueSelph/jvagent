"""AES-GCM encryption of OAuth access + refresh tokens at rest.

AUDIT-actions XC-1 Fix 2.
"""

import base64
import os
from unittest.mock import patch

import pytest

from jvagent.action.utils.oauth_token_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_token_from_storage,
    encrypt_token_for_storage,
    encryption_available,
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _key32() -> str:
    return _b64(b"\x01" * 32)


def test_no_key_returns_plaintext_unchanged():
    with patch.dict(
        os.environ,
        {"JVAGENT_TOKEN_ENC_KEY": "", "JVSPATIAL_JWT_SECRET_KEY": ""},
        clear=False,
    ):
        assert encryption_available() is False
        assert encrypt_token_for_storage("hello") == "hello"
        assert decrypt_token_from_storage("hello") == "hello"


def test_round_trip_with_explicit_key():
    with patch.dict(
        os.environ,
        {"JVAGENT_TOKEN_ENC_KEY": _key32()},
        clear=False,
    ):
        assert encryption_available() is True
        ct = encrypt_token_for_storage("ya29.access_token_payload_here")
        assert ct.startswith(CIPHER_PREFIX_V1)
        assert "ya29" not in ct  # plaintext should not appear in ciphertext
        assert decrypt_token_from_storage(ct) == "ya29.access_token_payload_here"


def test_round_trip_with_jwt_fallback():
    with patch.dict(
        os.environ,
        {
            "JVAGENT_TOKEN_ENC_KEY": "",
            "JVSPATIAL_JWT_SECRET_KEY": "some-deployment-jwt-secret",
        },
        clear=False,
    ):
        assert encryption_available() is True
        ct = encrypt_token_for_storage("refresh_token_value")
        assert ct.startswith(CIPHER_PREFIX_V1)
        assert decrypt_token_from_storage(ct) == "refresh_token_value"


def test_empty_input_returns_empty():
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": _key32()}, clear=False):
        assert encrypt_token_for_storage("") == ""
        assert decrypt_token_from_storage("") == ""


def test_legacy_plaintext_passes_through_decrypt():
    """Old rows persisted before encryption landed have no ``v1:`` prefix.
    Decrypt MUST return them unchanged so reads keep working."""
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": _key32()}, clear=False):
        assert decrypt_token_from_storage("legacy_plaintext_token") == (
            "legacy_plaintext_token"
        )


def test_ciphertext_corruption_yields_empty():
    """An attacker who flips a byte in the ciphertext should NOT be able
    to make decryption return arbitrary bytes — GCM auth tag detects
    tampering and we return empty so the caller forces re-auth."""
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": _key32()}, clear=False):
        ct = encrypt_token_for_storage("legit_token")
        # Flip a byte in the body — keep the ``v1:`` prefix.
        body = ct[len(CIPHER_PREFIX_V1) :]
        flipped_body = ("A" if body[5] != "A" else "B") + body[6:]
        tampered = CIPHER_PREFIX_V1 + body[:5] + flipped_body
        assert decrypt_token_from_storage(tampered) == ""


def test_key_rotation_via_previous_key():
    """A row encrypted under the old key must decrypt under the new
    deployment's PREVIOUS key while writes go to the new CURRENT key."""
    old_key = _b64(b"\x02" * 32)
    new_key = _b64(b"\x03" * 32)

    # Encrypt under the old key.
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": old_key}, clear=False):
        old_ct = encrypt_token_for_storage("rotating_token")

    # Switch to new key, declare old as PREVIOUS — decrypt must succeed.
    with patch.dict(
        os.environ,
        {
            "JVAGENT_TOKEN_ENC_KEY": new_key,
            "JVAGENT_TOKEN_ENC_KEY_PREVIOUS": old_key,
        },
        clear=False,
    ):
        assert decrypt_token_from_storage(old_ct) == "rotating_token"
        # Fresh writes use the new key (different ciphertext).
        new_ct = encrypt_token_for_storage("rotating_token")
        assert new_ct != old_ct


def test_key_rotation_without_previous_fails_old_ciphertext():
    """If operator forgets to set JVAGENT_TOKEN_ENC_KEY_PREVIOUS during
    rotation, old rows decrypt as empty — caller must re-auth."""
    old_key = _b64(b"\x04" * 32)
    new_key = _b64(b"\x05" * 32)

    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": old_key}, clear=False):
        old_ct = encrypt_token_for_storage("old_token")

    with patch.dict(
        os.environ,
        {"JVAGENT_TOKEN_ENC_KEY": new_key, "JVAGENT_TOKEN_ENC_KEY_PREVIOUS": ""},
        clear=False,
    ):
        assert decrypt_token_from_storage(old_ct) == ""


def test_malformed_b64_payload_returns_empty():
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": _key32()}, clear=False):
        assert decrypt_token_from_storage(CIPHER_PREFIX_V1 + "!!!bad!!!") == ""


def test_too_short_ciphertext_returns_empty():
    with patch.dict(os.environ, {"JVAGENT_TOKEN_ENC_KEY": _key32()}, clear=False):
        assert decrypt_token_from_storage(CIPHER_PREFIX_V1 + _b64(b"short")) == ""
