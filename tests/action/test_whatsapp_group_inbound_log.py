"""WhatsApp group inbound context (conversation id + sender name)."""

from types import SimpleNamespace

import pytest

from jvagent.action.whatsapp.modules.wwebjs_api import WWebJSAPI
from jvagent.action.whatsapp.utils.group_inbound_log import (
    build_group_inbound_context,
    group_title_from_chat_api_response,
)


@pytest.mark.asyncio
async def test_translate_wwebjs_sets_group_author_from_participant():
    wwebjs_data = {
        "dataType": "message",
        "sessionId": "test",
        "data": {
            "message": {
                "_data": {
                    "from": "120363428616636917@g.us",
                    "body": "hi",
                    "type": "chat",
                    "notifyName": "Staff User",
                    "id": {
                        "_serialized": "abc@g.us",
                        "participant": "5926178650@c.us",
                        "fromMe": False,
                    },
                }
            }
        },
    }
    wpp = await WWebJSAPI.translate_wwebjs_to_wppconnect(wwebjs_data)
    assert wpp["author"] == "5926178650@c.us"

    payload = await WWebJSAPI("http://test", "sess", "token").parse_inbound_message(
        wwebjs_data
    )
    assert payload is not None
    assert payload.author == "5926178650"
    assert payload.sender_name == "Staff User"
    assert payload.isGroup is True


def test_group_title_from_chat_api_response_subject():
    result = {
        "chat": {
            "name": "Silvie Team",
            "groupMetadata": {"subject": "Fallback Subject"},
        }
    }
    assert group_title_from_chat_api_response(result) == "Silvie Team"


def test_group_title_from_chat_api_response_metadata_only():
    result = {"data": {"groupMetadata": {"subject": "Ops Group"}}}
    assert group_title_from_chat_api_response(result) == "Ops Group"


def test_build_group_inbound_context_uses_sender_and_name():
    data = SimpleNamespace(
        sender="120363428616636917",
        author="5926178650",
        sender_name="Staff User",
    )
    ctx = build_group_inbound_context(data)
    assert ctx.group_id == "120363428616636917"
    assert ctx.sender_name == "Staff User"
    assert ctx.sender_phone == ""
    assert ctx.group_name == ""
