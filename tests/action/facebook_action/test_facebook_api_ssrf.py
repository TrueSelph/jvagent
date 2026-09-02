"""Facebook API outbound URL safety."""

from __future__ import annotations

from unittest.mock import patch

from jvagent.action.facebook_action.facebook_api import FacebookAPI


def _api() -> FacebookAPI:
    return FacebookAPI(
        api_url="https://graph.facebook.com/v18.0",
        app_secret="secret",
        app_id="app",
        page_id="page",
        page_access_token="tok",
        verify_token="verify",
    )


def test_get_mime_type_blocks_private_url_without_head():
    api = _api()
    with patch.object(FacebookAPI, "_outbound_head_url_allowed", return_value=False):
        result = api.get_mime_type(url="http://127.0.0.1/secret")

    assert result is None


def test_get_mime_type_allows_public_url_head():
    api = _api()
    with (
        patch.object(FacebookAPI, "_outbound_head_url_allowed", return_value=True),
        patch("jvagent.action.facebook_action.facebook_api.requests.head") as head,
    ):
        head.return_value.history = []
        head.return_value.headers = {"Content-Type": "image/jpeg"}
        result = api.get_mime_type(url="https://cdn.example.com/photo.jpg")

    assert result == {"file_type": "image", "mime": "image/jpeg"}
    head.assert_called_once()
