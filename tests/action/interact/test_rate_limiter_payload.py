"""Payload size validation on the public interact rate limiter (media-aware)."""

from __future__ import annotations

from jvagent.action.interact.rate_limiter import (
    DEFAULT_MAX_DATA_JSON_BYTES,
    DEFAULT_MAX_MEDIA_JSON_BYTES,
    InteractRateLimiter,
)


def test_validate_data_payload_accepts_small_dict():
    limiter = InteractRateLimiter(max_data_json_bytes=1024)
    ok, err = limiter.validate_data_payload({"image_urls": ["https://x.test/a.png"]})
    assert ok is True
    assert err is None


def test_validate_data_payload_rejects_oversized_control_data():
    # a non-media (control) key over the small cap is rejected
    limiter = InteractRateLimiter(max_data_json_bytes=64)
    ok, err = limiter.validate_data_payload({"foo": "x" * 200})
    assert ok is False
    assert err and "data (excluding uploaded media) exceeds maximum size" in err


def test_media_keys_exempt_from_control_cap():
    # a large base64 image under a tiny control cap is ACCEPTED because media
    # is validated against the (generous) media cap, not the control cap.
    limiter = InteractRateLimiter(
        max_data_json_bytes=64, max_media_json_bytes=10 * 1024 * 1024
    )
    blob = {"image_urls": [{"base64": "x" * 400_000, "mime_type": "image/png"}]}
    ok, err = limiter.validate_data_payload(blob)
    assert ok is True and err is None


def test_media_over_media_cap_rejected():
    limiter = InteractRateLimiter(max_data_json_bytes=64, max_media_json_bytes=1024)
    blob = {"image_urls": [{"base64": "x" * 4000}]}
    ok, err = limiter.validate_data_payload(blob)
    assert ok is False
    assert err and "uploaded media exceeds maximum size" in err


def test_mixed_control_and_media_each_checked_independently():
    limiter = InteractRateLimiter(
        max_data_json_bytes=4096, max_media_json_bytes=10 * 1024 * 1024
    )
    blob = {
        "channel_meta": {"k": "v"},  # small control
        "image_urls": [{"base64": "x" * 400_000}],  # large media
    }
    ok, err = limiter.validate_data_payload(blob)
    assert ok is True and err is None


def test_validate_data_payload_disabled_when_limits_none():
    limiter = InteractRateLimiter(max_data_json_bytes=None, max_media_json_bytes=None)
    ok, err = limiter.validate_data_payload(
        {"huge": "x" * 10000, "image_urls": [{"base64": "x" * 10_000_000}]}
    )
    assert ok is True
    assert err is None


def test_default_caps():
    assert DEFAULT_MAX_DATA_JSON_BYTES == 256 * 1024
    assert DEFAULT_MAX_MEDIA_JSON_BYTES == 20 * 1024 * 1024
