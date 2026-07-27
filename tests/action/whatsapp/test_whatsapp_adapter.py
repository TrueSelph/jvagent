"""Tests for WhatsApp ResponseBus adapter routing (cloud / CTA / media)."""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from jvagent.action.response.message import ResponseMessage
from jvagent.action.whatsapp.whatsapp_adapter import WhatsAppAdapter


class _FakeApi:
    def __init__(self) -> None:
        self.cloud_calls: List[Any] = []
        self.cta_calls: List[Dict[str, Any]] = []
        self.text_calls: List[Dict[str, Any]] = []

    async def send_cloud_message(
        self, phone: str, message: Optional[dict] = None
    ) -> dict:
        self.cloud_calls.append((phone, message))
        return {"ok": True}

    async def send_cta_url_message(self, phone: str, **kwargs: Any) -> dict:
        self.cta_calls.append({"phone": phone, **kwargs})
        return {"ok": True}

    async def send_message(self, phone: str, message: str, **kwargs: Any) -> dict:
        self.text_calls.append({"phone": phone, "message": message, **kwargs})
        return {"ok": True}

    async def set_typing_status(self, **kwargs: Any) -> dict:
        return {"ok": True}


@pytest.fixture
def adapter_and_api():
    api = _FakeApi()
    action = SimpleNamespace(
        chunk_length=1024,
        is_configured=lambda: True,
        api=AsyncMock(return_value=api),
    )
    adapter = WhatsAppAdapter(action=action)
    return adapter, api


def _msg(**kwargs: Any) -> ResponseMessage:
    defaults = {
        "user_id": "16505551234",
        "session_id": "s1",
        "content": "",
        "channel": "whatsapp",
        "message_type": "adhoc",
        "metadata": {},
    }
    defaults.update(kwargs)
    return ResponseMessage(**defaults)


@pytest.mark.asyncio
async def test_adapter_routes_whatsapp_cloud_message(adapter_and_api):
    adapter, api = adapter_and_api
    payload = {
        "type": "sticker",
        "sticker": {"id": "media-sticker-1"},
    }
    ok = await adapter.send(_msg(metadata={"whatsapp_cloud_message": payload}))
    assert ok is True
    assert api.cloud_calls == [("16505551234", payload)]
    assert api.cta_calls == []
    assert api.text_calls == []


@pytest.mark.asyncio
async def test_adapter_routes_cta_url_metadata(adapter_and_api):
    adapter, api = adapter_and_api
    ok = await adapter.send(
        _msg(
            content="Tap to pay",
            metadata={
                "cta_url": "https://pay.example/invoice/Z1",
                "cta_display_text": "Pay now",
                "cta_header": "Invoice Z1",
                "cta_footer": "Zoon",
            },
        )
    )
    assert ok is True
    assert len(api.cta_calls) == 1
    call = api.cta_calls[0]
    assert call["phone"] == "16505551234"
    assert call["url"] == "https://pay.example/invoice/Z1"
    assert call["body"] == "Tap to pay"
    assert call["display_text"] == "Pay now"
    assert call["header"] == "Invoice Z1"
    assert call["footer"] == "Zoon"
    assert api.cloud_calls == []


@pytest.mark.asyncio
async def test_adapter_cta_body_from_metadata_overrides_content(adapter_and_api):
    adapter, api = adapter_and_api
    ok = await adapter.send(
        _msg(
            content="ignored",
            metadata={
                "cta_url": "https://example.com",
                "cta_body": "Open your quote",
            },
        )
    )
    assert ok is True
    assert api.cta_calls[0]["body"] == "Open your quote"


@pytest.mark.asyncio
async def test_adapter_cloud_message_precedes_cta(adapter_and_api):
    adapter, api = adapter_and_api
    ok = await adapter.send(
        _msg(
            content="body",
            metadata={
                "whatsapp_cloud_message": {"type": "text", "text": {"body": "raw"}},
                "cta_url": "https://example.com",
            },
        )
    )
    assert ok is True
    assert len(api.cloud_calls) == 1
    assert api.cta_calls == []
