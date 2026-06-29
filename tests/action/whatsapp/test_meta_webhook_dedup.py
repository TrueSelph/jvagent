"""Tests for Meta webhook wamid dedup and parse hardening guards."""

import pytest

from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI
from jvagent.action.whatsapp.utils.meta_webhook_dedup import (
    clear_meta_wamid_cache,
    remember_meta_wamid,
)

from .test_meta_api import SAMPLE_TEXT_WEBHOOK, STATUS_ONLY_WEBHOOK

WAMID_A = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA="
WAMID_B = "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI3MTE5MjVBOTE3MDk5QUVFM0YA"


@pytest.fixture(autouse=True)
def _clear_dedup_cache():
    clear_meta_wamid_cache()
    yield
    clear_meta_wamid_cache()


@pytest.fixture
def meta_api():
    return MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0/",
        session="106540352242922",
        token="test-token",
        phone_number_id="106540352242922",
    )


class TestMetaWamidDedup:
    def test_first_wamid_is_new(self):
        assert remember_meta_wamid(WAMID_A) is True

    def test_duplicate_wamid_is_rejected(self):
        assert remember_meta_wamid(WAMID_A) is True
        assert remember_meta_wamid(WAMID_A) is False

    def test_different_wamids_both_accepted(self):
        assert remember_meta_wamid(WAMID_A) is True
        assert remember_meta_wamid(WAMID_B) is True

    def test_empty_wamid_does_not_consume_dedup_slot(self):
        assert remember_meta_wamid("") is True
        assert remember_meta_wamid("") is True
        assert remember_meta_wamid(WAMID_A) is True

    def test_status_wamid_not_stored_by_parser(self, meta_api):
        """Status-only webhooks have empty message_id; must not block real messages."""
        assert MetaWhatsAppAPI._webhook_has_statuses_only(STATUS_ONLY_WEBHOOK) is True


class TestMetaParseGuards:
    @pytest.mark.asyncio
    async def test_status_only_webhook_ignored(self, meta_api):
        payload = await meta_api.parse_inbound_message(STATUS_ONLY_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "ignored"
        assert payload.message_id == ""

    @pytest.mark.asyncio
    async def test_status_only_does_not_consume_wamid_dedup(self, meta_api):
        await meta_api.parse_inbound_message(STATUS_ONLY_WEBHOOK)
        payload = await meta_api.parse_inbound_message(SAMPLE_TEXT_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "chat"
        assert payload.message_id == WAMID_A
        assert remember_meta_wamid(WAMID_A) is True

    @pytest.mark.asyncio
    async def test_reaction_type_ignored(self, meta_api):
        webhook = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "106540352242922"},
                                "messages": [
                                    {
                                        "from": "16505551234",
                                        "id": "wamid.reaction123",
                                        "type": "reaction",
                                        "reaction": {
                                            "message_id": WAMID_A,
                                            "emoji": "👍",
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        payload = await meta_api.parse_inbound_message(webhook)
        assert payload is not None
        assert payload.message_type == "ignored"
        assert payload.message_id == "wamid.reaction123"

    @pytest.mark.asyncio
    async def test_system_type_ignored(self, meta_api):
        webhook = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "106540352242922"},
                                "messages": [
                                    {
                                        "from": "16505551234",
                                        "id": "wamid.system123",
                                        "type": "system",
                                        "system": {"body": "User changed number"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        payload = await meta_api.parse_inbound_message(webhook)
        assert payload is not None
        assert payload.message_type == "ignored"

    @pytest.mark.asyncio
    async def test_text_message_still_parsed(self, meta_api):
        payload = await meta_api.parse_inbound_message(SAMPLE_TEXT_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "chat"
        assert payload.message_id == WAMID_A
