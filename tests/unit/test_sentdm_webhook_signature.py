"""SentDM inbound webhook signature verification (Sent ``v1,`` + legacy hex)."""

import base64
import hashlib
import hmac
import time

from jvagent.action.sentdm_broadcast.endpoints import _verify_sentdm_signature


def test_sentdm_v1_signature_verifies() -> None:
    raw_key = b"unit-test-signing-key"
    secret = "whsec_" + base64.b64encode(raw_key).decode("ascii")
    webhook_id = "wh_test_endpoint"
    ts = str(int(time.time()))
    raw_body = b'{"field":"message.status","timestamp":1,"payload":{}}'
    signed = f"{webhook_id}.{ts}.{raw_body.decode('utf-8')}"
    digest = hmac.new(raw_key, signed.encode("utf-8"), hashlib.sha256).digest()
    header = "v1," + base64.b64encode(digest).decode("ascii").rstrip("=")

    assert _verify_sentdm_signature(
        secret,
        raw_body,
        header,
        webhook_id=webhook_id,
        timestamp=ts,
    )


def test_sentdm_legacy_hex_still_verifies() -> None:
    secret = "plain-test-secret"
    raw_body = b'{"x":1}'
    sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    assert _verify_sentdm_signature(
        secret,
        raw_body,
        sig,
        webhook_id="ignored",
        timestamp="ignored",
    )
