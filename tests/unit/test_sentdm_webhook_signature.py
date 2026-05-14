"""SentDM inbound webhook signature verification (Sent ``v1,`` + legacy hex)."""

import base64
import hashlib
import hmac
import time

from jvagent.action.sentdm_broadcast.endpoints import (
    _normalize_sentdm_webhook_envelope,
    _verify_sentdm_signature,
)
from jvagent.action.sentdm_broadcast.sentdm_broadcast_action import (
    SentDMBroadcastAction,
    _extract_sentdm_webhook_list_items,
    _sentdm_webhook_url_on_public_origin,
    _sentdm_webhook_urls_equivalent,
)


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


def test_derive_status_from_message_status() -> None:
    st, err = SentDMBroadcastAction._derive_status_and_error(
        "message",
        {"message_id": "x", "message_status": "READ"},
    )
    assert st == "read"
    assert err is None


def test_normalize_dashboard_wrapped_envelope() -> None:
    body = {
        "eventType": "message.read",
        "eventData": {
            "field": "message",
            "sub_type": "message.read",
            "timestamp": "2026-05-14T14:30:28Z",
            "payload": {
                "message_id": "mid-1",
                "message_status": "READ",
                "channel": "sms",
            },
        },
    }
    field, fold = _normalize_sentdm_webhook_envelope(body)
    assert field == "message"
    assert fold.get("message_id") == "mid-1"
    assert fold.get("sub_type") == "message.read"
    st, _ = SentDMBroadcastAction._derive_status_and_error(field, fold)
    assert st == "read"


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


def test_sentdm_webhook_path_prefix_for_base() -> None:
    assert (
        SentDMBroadcastAction._sentdm_webhook_path_prefix_for_base("https://ex.com")
        == "https://ex.com/api/sentdm/webhook/"
    )
    assert (
        SentDMBroadcastAction._sentdm_webhook_path_prefix_for_base("https://ex.com/")
        == "https://ex.com/api/sentdm/webhook/"
    )


def test_extract_sentdm_webhook_list_nested_data() -> None:
    body = {
        "data": {
            "webhooks": [
                {"id": "w1", "endpoint_url": "https://h/api/sentdm/webhook/a?api_key=x"}
            ]
        }
    }
    rows = _extract_sentdm_webhook_list_items(body)
    assert len(rows) == 1
    assert rows[0]["id"] == "w1"


def test_sentdm_webhook_urls_equivalent_ignores_query() -> None:
    a = "https://h/api/sentdm/webhook/n.Action.1?api_key=jv_abc"
    b = "https://h/api/sentdm/webhook/n.Action.1"
    assert _sentdm_webhook_urls_equivalent(a, b)
    assert _sentdm_webhook_url_on_public_origin(a, "https://h")


def test_sentdm_webhook_different_paths_not_equivalent() -> None:
    a = "https://h/api/sentdm/webhook/n.Action.1?k=1"
    b = "https://h/api/sentdm/webhook/n.Action.2?k=2"
    assert not _sentdm_webhook_urls_equivalent(a, b)
