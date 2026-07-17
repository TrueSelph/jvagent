"""sanitize_visitor_data_for_log must redact base64 / media keys."""

from jvagent.action.interact.webhook_pipeline import sanitize_visitor_data_for_log


def test_redacts_image_urls_data_uri():
    b64 = "iVBORw0KGgo" + ("A" * 500)
    data = {"image_urls": [f"data:image/png;base64,{b64}"]}
    out = sanitize_visitor_data_for_log(data)
    assert "image_urls" in out
    assert isinstance(out["image_urls"], list)
    assert "redacted" in out["image_urls"][0]
    assert b64 not in out["image_urls"][0]


def test_redacts_email_attachments():
    big = "AAAA" * 100
    data = {
        "email_attachments": [
            {"filename": "x.png", "content_base64": big},
        ]
    }
    out = sanitize_visitor_data_for_log(data)
    assert big not in str(out)
    assert "redacted" in str(out["email_attachments"])


def test_preserves_small_non_media():
    data = {"channel": "email", "note": "hello"}
    assert sanitize_visitor_data_for_log(data) == data
