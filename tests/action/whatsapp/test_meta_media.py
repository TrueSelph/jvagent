"""Tests for Meta WhatsApp media, voice, and typing parity."""

import base64
from typing import Optional

import pytest

from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI

PHONE_ID = "106540352242922"
SENDER = "16505551234"
WAMID = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA="

META_BASE_VALUE = {
    "messaging_product": "whatsapp",
    "metadata": {
        "display_phone_number": "15550783881",
        "phone_number_id": PHONE_ID,
    },
    "contacts": [{"profile": {"name": "Sheena Nelson"}, "wa_id": SENDER}],
}


def _meta_webhook(message: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {"value": {**META_BASE_VALUE, "messages": [message]}, "field": "messages"}
                ],
            }
        ],
    }


SAMPLE_IMAGE_WEBHOOK = _meta_webhook(
    {
        "from": SENDER,
        "id": WAMID,
        "timestamp": "1749416383",
        "type": "image",
        "image": {
            "id": "715960496164079",
            "mime_type": "image/jpeg",
            "caption": "What is this?",
            "sha256": "abc",
        },
    }
)

SAMPLE_VOICE_WEBHOOK = _meta_webhook(
    {
        "from": SENDER,
        "id": WAMID,
        "timestamp": "1749416383",
        "type": "audio",
        "audio": {
            "id": "715960496164080",
            "mime_type": "audio/ogg; codecs=opus",
            "voice": True,
        },
    }
)

SAMPLE_LOCATION_WEBHOOK = _meta_webhook(
    {
        "from": SENDER,
        "id": WAMID,
        "timestamp": "1749416383",
        "type": "location",
        "location": {"latitude": 37.77, "longitude": -122.42, "name": "HQ"},
    }
)

SAMPLE_DOCUMENT_WEBHOOK = _meta_webhook(
    {
        "from": SENDER,
        "id": WAMID,
        "timestamp": "1749416383",
        "type": "document",
        "document": {
            "id": "715960496164081",
            "mime_type": "application/pdf",
            "filename": "report.pdf",
        },
    }
)


@pytest.fixture
def meta_api():
    return MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0/",
        session=PHONE_ID,
        token="test-token",
        phone_number_id=PHONE_ID,
    )


class TestMetaInboundMedia:
    @pytest.mark.asyncio
    async def test_parses_image_with_downloaded_media(self, meta_api):
        fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"

        async def fake_download(media_id: str):
            assert media_id == "715960496164079"
            return fake_bytes, "image/jpeg"

        meta_api.download_media = fake_download  # type: ignore[method-assign]

        payload = await meta_api.parse_inbound_message(SAMPLE_IMAGE_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "image"
        assert payload.body == "What is this?"
        assert payload.mime_type == "image/jpeg"
        assert payload.media == base64.b64encode(fake_bytes).decode("ascii")
        assert payload.message_id == WAMID

    @pytest.mark.asyncio
    async def test_parses_voice_as_ptt(self, meta_api):
        async def fake_download(media_id: str):
            return b"OggS", "audio/ogg; codecs=opus"

        meta_api.download_media = fake_download  # type: ignore[method-assign]

        payload = await meta_api.parse_inbound_message(SAMPLE_VOICE_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "ptt"
        assert payload.media

    @pytest.mark.asyncio
    async def test_parses_location(self, meta_api):
        payload = await meta_api.parse_inbound_message(SAMPLE_LOCATION_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "location"
        assert payload.location["latitude"] == 37.77
        assert payload.location["longitude"] == -122.42

    @pytest.mark.asyncio
    async def test_parses_document(self, meta_api):
        async def fake_download(media_id: str):
            return b"%PDF", "application/pdf"

        meta_api.download_media = fake_download  # type: ignore[method-assign]

        payload = await meta_api.parse_inbound_message(SAMPLE_DOCUMENT_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "document"
        assert payload.filename == "report.pdf"

    @pytest.mark.asyncio
    async def test_image_ignored_when_download_fails(self, meta_api):
        async def fake_download(media_id: str):
            return b"", ""

        meta_api.download_media = fake_download  # type: ignore[method-assign]

        payload = await meta_api.parse_inbound_message(SAMPLE_IMAGE_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "ignored"


class TestMetaTyping:
    @pytest.mark.asyncio
    async def test_typing_with_message_id_uses_read_and_typing(self, meta_api):
        captured = {}

        async def fake_request(url, method, headers, data=None, params=None, json_body=True):
            captured["data"] = data
            return {"messaging_product": "whatsapp"}

        meta_api._make_request = fake_request  # type: ignore[method-assign]

        await meta_api.set_typing_status(
            "16505551234", value=True, message_id=WAMID
        )
        assert captured["data"]["status"] == "read"
        assert captured["data"]["message_id"] == WAMID
        assert captured["data"]["typing_indicator"] == {"type": "text"}


class TestMetaOutboundMedia:
    @pytest.mark.asyncio
    async def test_send_image_uploads_and_sends(self, meta_api):
        upload_calls = []
        send_calls = []

        async def fake_fetch(url: str):
            return b"imgbytes", "image/png"

        async def fake_upload(file_bytes, mime_type, filename="file"):
            upload_calls.append((mime_type, filename))
            return "media-id-123"

        async def fake_send(phone, msg_type, media_id, caption="", context_id="", extra_media_fields=None):
            send_calls.append((msg_type, media_id, caption))
            return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.out"}]}

        meta_api._fetch_url_bytes = fake_fetch  # type: ignore[method-assign]
        meta_api._upload_media = fake_upload  # type: ignore[method-assign]
        meta_api._send_media_message = fake_send  # type: ignore[method-assign]

        result = await meta_api.send_image(
            "16505551234", "https://example.com/a.png", caption="Hi"
        )
        assert result.get("ok") is True
        assert upload_calls[0][0] == "image/png"
        assert send_calls[0] == ("image", "media-id-123", "Hi")

    @pytest.mark.asyncio
    async def test_send_voice_mp3_without_voice_flag(self, meta_api):
        extra_fields: list[Optional[dict]] = []

        async def fake_fetch(url: str):
            return b"mp3data", "audio/mpeg"

        async def fake_upload(file_bytes, mime_type, filename="file"):
            return "audio-media-id"

        async def fake_send(phone, msg_type, media_id, caption="", context_id="", extra_media_fields=None):
            extra_fields.append(extra_media_fields)
            return {"messaging_product": "whatsapp"}

        meta_api._fetch_url_bytes = fake_fetch  # type: ignore[method-assign]
        meta_api._upload_media = fake_upload  # type: ignore[method-assign]
        meta_api._send_media_message = fake_send  # type: ignore[method-assign]

        result = await meta_api.send_voice(
            "16505551234", "https://example.com/voice.mp3", is_ptt=True
        )
        assert result.get("ok") is True
        assert extra_fields[0] == {}

    @pytest.mark.asyncio
    async def test_send_voice_ogg_sets_voice_flag(self, meta_api):
        extra_fields: list[Optional[dict]] = []

        async def fake_fetch(url: str):
            return b"OggS", "audio/ogg; codecs=opus"

        async def fake_upload(file_bytes, mime_type, filename="file"):
            return "voice-media-id"

        async def fake_send(phone, msg_type, media_id, caption="", context_id="", extra_media_fields=None):
            extra_fields.append(extra_media_fields)
            return {"messaging_product": "whatsapp"}

        meta_api._fetch_url_bytes = fake_fetch  # type: ignore[method-assign]
        meta_api._upload_media = fake_upload  # type: ignore[method-assign]
        meta_api._send_media_message = fake_send  # type: ignore[method-assign]

        result = await meta_api.send_voice(
            "16505551234", "https://example.com/voice.ogg", is_ptt=True
        )
        assert result.get("ok") is True
        assert extra_fields[0] == {"voice": True}

    @pytest.mark.asyncio
    async def test_set_recording_status_noop(self, meta_api):
        result = await meta_api.set_recording_status("16505551234", value=True)
        assert result.get("ok") is True
        assert result.get("reason") == "meta_cloud_api"
