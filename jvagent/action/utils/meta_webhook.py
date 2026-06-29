"""Shared Meta webhook signature verification (Messenger, WhatsApp Cloud API)."""

import hashlib
import hmac

from fastapi import Request


def verify_meta_webhook_signature(
    raw_body: bytes, request: Request, app_secret: str
) -> bool:
    """Verify ``X-Hub-Signature-256`` using Meta app secret (SHA256 HMAC, hex digest)."""
    secret = (app_secret or "").strip()
    if not secret:
        return False
    sig_header = request.headers.get("x-hub-signature-256")
    if not sig_header:
        return False
    signature = str(sig_header).strip()
    if signature.startswith("sha256="):
        signature = signature[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
