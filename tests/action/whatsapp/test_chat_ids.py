"""Tests for WhatsApp chat id helpers."""

from jvagent.action.whatsapp.utils.chat_ids import (
    is_group_whatsapp_turn,
    is_valid_whatsapp_phone,
    is_whatsapp_group_chat_id,
    lid_jid_for_conversion,
    participant_phone_from_payload,
    raw_author_from_payload,
    strip_whatsapp_suffix,
)


def test_strip_whatsapp_suffix():
    assert strip_whatsapp_suffix("5926431530@c.us") == "5926431530"
    assert strip_whatsapp_suffix("120363428616636917@g.us") == "120363428616636917"


def test_is_whatsapp_group_chat_id():
    assert is_whatsapp_group_chat_id("120363428616636917@g.us")
    assert is_whatsapp_group_chat_id("120363428616636917")
    assert not is_whatsapp_group_chat_id("5926431530")
    assert not is_whatsapp_group_chat_id("+5926431530")


def test_is_valid_whatsapp_phone_e164_boundary():
    assert is_valid_whatsapp_phone("5926431530")
    assert not is_valid_whatsapp_phone("120363428616636917")
    assert not is_valid_whatsapp_phone("12345")


def test_is_group_whatsapp_turn():
    group_id = "120363428616636917"
    assert is_group_whatsapp_turn({"isGroup": True}, "592111")
    assert is_group_whatsapp_turn({}, group_id)
    assert not is_group_whatsapp_turn({}, "5926431530")


def test_raw_author_and_lid_jid():
    payload = {"author": "999888777@lid"}
    assert raw_author_from_payload(payload) == "999888777"
    assert lid_jid_for_conversion("999888777") == "999888777@lid"
    assert lid_jid_for_conversion("999888777@lid") == "999888777@lid"


def test_participant_phone_from_payload():
    payload = {
        "isGroup": True,
        "sender": "120363428616636917",
        "author": "5923333333",
    }
    assert participant_phone_from_payload(payload) == "5923333333"
    assert (
        participant_phone_from_payload({"isGroup": False, "author": "5923333333"}) == ""
    )
    assert (
        participant_phone_from_payload(
            {"isGroup": True, "author": "120363428616636917"}
        )
        == ""
    )

    assert (
        participant_phone_from_payload(
            {"isGroup": False, "author": "5924444444"},
            user_id="120363428616636917",
        )
        == "5924444444"
    )
