"""Tests for Messenger webhook parsing and Meta signature verification."""

from jvspatial.api.integrations.webhooks.utils import generate_hmac_signature
from starlette.requests import Request

from jvagent.action.facebook_action.facebook_action import FacebookAction
from jvagent.action.facebook_action.facebook_api import FacebookAPI
from jvagent.action.facebook_action.messenger_webhook_helpers import (
    MESSENGER_DEFAULT_MEDIA_UTTERANCE,
    resolve_messenger_inbound_event,
    verify_meta_messenger_signature,
)


def _req_with_sig(body: bytes, signature: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "method": "POST",
            "path": "/",
            "headers": [(b"x-hub-signature-256", signature.encode("ascii"))],
        }
    )


class TestIterMessengerUserTextEvents:
    def test_empty_non_page_object(self):
        assert FacebookAPI.iter_messenger_user_text_events({}) == []
        assert (
            FacebookAPI.iter_messenger_user_text_events({"object": "instagram"}) == []
        )

    def test_skips_feed_changes(self):
        body = {
            "object": "page",
            "entry": [{"id": "PAGE1", "changes": [{"value": {}}]}],
        }
        assert FacebookAPI.iter_messenger_user_text_events(body) == []

    def test_skips_delivery_read_echo_attachment_without_url_postback(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {"sender": {"id": "u1"}, "delivery": {"mids": ["x"]}},
                        {"sender": {"id": "u1"}, "read": {"watermark": 1}},
                        {
                            "sender": {"id": "u1"},
                            "message": {"text": "echo", "is_echo": True},
                        },
                        {
                            "sender": {"id": "u1"},
                            "message": {"attachments": [{"type": "image"}]},
                        },
                        {"sender": {"id": "u1"}, "postback": {"payload": "START"}},
                    ],
                }
            ],
        }
        assert FacebookAPI.iter_messenger_user_text_events(body) == []

    def test_extracts_attachment_only_when_payload_url_present(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {
                            "sender": {"id": "u1"},
                            "message": {
                                "mid": "m1",
                                "attachments": [
                                    {
                                        "type": "image",
                                        "payload": {"url": "https://cdn.example/a.jpg"},
                                    },
                                    {
                                        "type": "image",
                                        "payload": {"url": "https://cdn.example/b.jpg"},
                                    },
                                ],
                            },
                        },
                    ],
                }
            ],
        }
        ev = FacebookAPI.iter_messenger_user_text_events(body)
        assert len(ev) == 1
        assert ev[0]["sender_id"] == "u1"
        assert ev[0]["message"] == ""
        assert len(ev[0]["attachments"]) == 2

    def test_extracts_text_with_image_attachments(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {
                            "sender": {"id": "u1"},
                            "message": {
                                "text": "caption here",
                                "attachments": [
                                    {
                                        "type": "image",
                                        "payload": {"url": "https://cdn.example/a.jpg"},
                                    },
                                ],
                            },
                        },
                    ],
                }
            ],
        }
        ev = FacebookAPI.iter_messenger_user_text_events(body)
        assert len(ev) == 1
        assert ev[0]["message"] == "caption here"

    def test_extracts_audio_attachment_with_url(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {
                            "sender": {"id": "u1"},
                            "message": {
                                "attachments": [
                                    {
                                        "type": "audio",
                                        "payload": {
                                            "url": "https://cdn.example/clip.mp4"
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                }
            ],
        }
        ev = FacebookAPI.iter_messenger_user_text_events(body)
        assert len(ev) == 1
        assert ev[0]["message"] == ""
        assert ev[0]["attachments"][0]["type"] == "audio"

    def test_extracts_user_text_multiple(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {"sender": {"id": "u1"}, "message": {"text": "  hi  "}},
                        {"sender": {"id": "u2"}, "message": {"text": "there"}},
                    ],
                }
            ],
        }
        ev = FacebookAPI.iter_messenger_user_text_events(body)
        assert len(ev) == 2
        assert ev[0]["sender_id"] == "u1"
        assert ev[0]["message"] == "hi"
        assert ev[0]["page_id"] == "PAGE1"
        assert ev[1]["sender_id"] == "u2"
        assert ev[1]["message"] == "there"

    def test_extracts_location_attachment_without_url(self):
        body = {
            "object": "page",
            "entry": [
                {
                    "id": "PAGE1",
                    "messaging": [
                        {
                            "sender": {"id": "u1"},
                            "message": {
                                "mid": "m-loc",
                                "attachments": [
                                    {
                                        "type": "location",
                                        "payload": {
                                            "coordinates": {
                                                "lat": 37.42,
                                                "long": -122.08,
                                            }
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                }
            ],
        }
        ev = FacebookAPI.iter_messenger_user_text_events(body)
        assert len(ev) == 1
        assert ev[0]["sender_id"] == "u1"
        assert ev[0]["message"] == ""
        assert ev[0]["attachments"][0]["type"] == "location"


class TestResolveMessengerInboundEvent:
    async def test_image_only_populates_image_urls(self):
        event = {
            "sender_name": "",
            "sender_id": "u1",
            "page_id": "p1",
            "message_type": "message",
            "message": "",
            "attachments": [
                {"type": "image", "payload": {"url": "https://cdn.example/a.jpg"}},
                {"type": "image", "payload": {"url": "https://cdn.example/b.jpg"}},
            ],
            "mid": "mid1",
        }
        out = await resolve_messenger_inbound_event(event, agent=None, fb_action=None)
        assert out is not None
        utterance, data = out
        assert utterance == MESSENGER_DEFAULT_MEDIA_UTTERANCE
        assert data["image_urls"] == [
            "https://cdn.example/a.jpg",
            "https://cdn.example/b.jpg",
        ]

    async def test_audio_only_without_stt_returns_none(self):
        event = {
            "message": "",
            "attachments": [
                {"type": "audio", "payload": {"url": "https://cdn.example/a.mp4"}},
            ],
        }
        out = await resolve_messenger_inbound_event(event, agent=None, fb_action=None)
        assert out is None

    async def test_video_only_yields_synthetic_utterance(self):
        event = {
            "message": "",
            "attachments": [
                {"type": "video", "payload": {"url": "https://cdn.example/v.mp4"}},
            ],
        }
        out = await resolve_messenger_inbound_event(event, agent=None, fb_action=None)
        assert out is not None
        utterance, data = out
        assert utterance == "[User sent a video attachment]"
        assert "image_urls" not in data

    async def test_file_only_yields_synthetic_utterance(self):
        event = {
            "message": "",
            "attachments": [
                {"type": "file", "payload": {"url": "https://cdn.example/doc.pdf"}},
            ],
        }
        out = await resolve_messenger_inbound_event(event, agent=None, fb_action=None)
        assert out is not None
        utterance, _ = out
        assert utterance == "[User sent a file]"

    async def test_location_only_utterance(self):
        event = {
            "sender_id": "u1",
            "message": "",
            "attachments": [
                {
                    "type": "location",
                    "payload": {"coordinates": {"lat": 40.7, "long": -74.0}},
                },
            ],
        }
        out = await resolve_messenger_inbound_event(event, agent=None, fb_action=None)
        assert out is not None
        utterance, data = out
        assert utterance == "Location: 40.7, -74.0"
        assert "image_urls" not in data

    async def test_audio_transcription_merged_with_text(self, monkeypatch):
        class _MockSTT:
            async def invoke_base64(self, audio_base64: str, audio_type: str):
                assert audio_base64
                return "heard from clip"

        class _MockFB:
            stt_action = "MockSTT"
            tts_action = ""

            def _apply_env_defaults(self) -> None:
                pass

            def api(self):
                class _API:
                    page_access_token = "test_page_token"

                return _API()

            async def get_action(self, label: str):
                assert label == "MockSTT"
                return _MockSTT()

        def _fake_download(url: str, page_access_token: str, timeout: int = 60):
            assert page_access_token == "test_page_token"
            return b"\xff\xd6fake_audio_bytes", "audio/mp4"

        monkeypatch.setattr(
            FacebookAPI,
            "download_messenger_attachment",
            staticmethod(_fake_download),
        )

        event = {
            "sender_id": "psid1",
            "message": "hello",
            "attachments": [
                {"type": "audio", "payload": {"url": "https://cdn.example/x.mp4"}},
            ],
        }
        out = await resolve_messenger_inbound_event(
            event, agent=None, fb_action=_MockFB()
        )
        assert out is not None
        utterance, _data = out
        assert utterance == "hello\nheard from clip"


class TestVerifyMetaMessengerSignature:
    def test_valid_signature(self):
        secret = "app_secret_test"  # pragma: allowlist secret
        body = b'{"object":"page","entry":[]}'
        sig = generate_hmac_signature(body, secret)
        req = _req_with_sig(body, sig)
        assert verify_meta_messenger_signature(body, req, secret) is True

    def test_valid_when_legacy_sha1_header_also_present(self):
        """Meta may send X-Hub-Signature (sha1) first; verification must use -256 only."""
        secret = "app_secret_test"  # pragma: allowlist secret
        body = b'{"object":"page","entry":[]}'
        sig_256 = generate_hmac_signature(body, secret)
        req = Request(
            {
                "type": "http",
                "asgi": {"spec_version": "2.3", "version": "3.0"},
                "method": "POST",
                "path": "/",
                "headers": [
                    (
                        b"x-hub-signature",
                        b"sha1=0000000000000000000000000000000000000000",
                    ),
                    (b"x-hub-signature-256", sig_256.encode("ascii")),
                ],
            }
        )
        assert verify_meta_messenger_signature(body, req, secret) is True

    def test_invalid_signature(self):
        body = b"{}"
        req = _req_with_sig(body, "sha256=deadbeef")
        assert verify_meta_messenger_signature(body, req, "other_secret") is False

    def test_missing_header(self):
        req = Request(
            {
                "type": "http",
                "asgi": {"spec_version": "2.3", "version": "3.0"},
                "method": "POST",
                "path": "/",
                "headers": [],
            }
        )
        assert verify_meta_messenger_signature(b"{}", req, "secret") is False


class TestParseMessengerWebhookVerify:
    def test_challenge_ok(self):
        fb = FacebookAction()
        fb.verify_token = "my_verify"
        out = fb.parse_messenger_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "my_verify",
                "hub.challenge": "999",
            }
        )
        assert out == "999"

    def test_challenge_invalid_token(self):
        fb = FacebookAction()
        fb.verify_token = "expected"
        out = fb.parse_messenger_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "999",
            }
        )
        assert isinstance(out, dict)
        assert out.get("code") == 403

    def test_empty_verify_token_fails_closed(self):
        fb = FacebookAction()
        fb.verify_token = ""
        out = fb.parse_messenger_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "",
                "hub.challenge": "999",
            }
        )
        assert isinstance(out, dict)
        assert out.get("code") == 403


class TestDownloadMessengerAttachment:
    def test_blocks_non_meta_host(self, monkeypatch):
        called = {"n": 0}

        def _fake_get(*_a, **_k):
            called["n"] += 1
            raise AssertionError("requests.get should not run for blocked hosts")

        monkeypatch.setattr(
            "jvagent.action.facebook_action.facebook_api.requests.get", _fake_get
        )
        out = FacebookAPI.download_messenger_attachment(
            "https://evil.example/steal", "page_token"
        )
        assert out == (None, None)
        assert called["n"] == 0

    def test_uses_bearer_header_on_allowlisted_host(self, monkeypatch):
        seen: dict = {}

        class _Resp:
            content = b"abc"
            headers = {"Content-Type": "image/jpeg"}

            def iter_content(self, chunk_size=65536):
                yield self.content

            @staticmethod
            def raise_for_status():
                return None

        def _fake_get(url, headers=None, timeout=60, stream=False):
            seen["url"] = url
            seen["headers"] = headers
            seen["timeout"] = timeout
            seen["stream"] = stream
            return _Resp()

        monkeypatch.setattr(
            "jvagent.action.facebook_action.facebook_api.requests.get", _fake_get
        )
        out = FacebookAPI.download_messenger_attachment(
            "https://lookaside.fbsbx.com/ig/media.jpg", "page_token"
        )
        assert out[0] == b"abc"
        assert seen["headers"] == {"Authorization": "Bearer page_token"}
        assert "access_token" not in seen["url"]
        assert seen["stream"] is True

    def test_rejects_oversized_content_length(self, monkeypatch):
        class _Resp:
            headers = {"Content-Type": "image/jpeg", "Content-Length": "99999999"}

            def iter_content(self, chunk_size=65536):
                yield b"should-not-read"

            @staticmethod
            def raise_for_status():
                return None

        monkeypatch.setattr(
            "jvagent.action.facebook_action.facebook_api.requests.get",
            lambda *a, **k: _Resp(),
        )
        out = FacebookAPI.download_messenger_attachment(
            "https://lookaside.fbsbx.com/ig/media.jpg", "page_token"
        )
        assert out == (None, None)


class TestMetaCallbackUrlForSubscription:
    def test_strips_api_key_query(self):
        full = (
            "https://example.com/api/messenger/interact/webhook/n.Agent.x"
            "?api_key=jv_secret"
        )
        assert FacebookAction.meta_callback_url_for_subscription(full) == (
            "https://example.com/api/messenger/interact/webhook/n.Agent.x"
        )

    def test_no_query_unchanged(self):
        base = "https://example.com/api/messenger/interact/webhook/agent1"
        assert FacebookAction.meta_callback_url_for_subscription(base) == base

    def test_empty_string(self):
        assert FacebookAction.meta_callback_url_for_subscription("") == ""
