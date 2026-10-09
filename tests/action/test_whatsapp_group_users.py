"""WhatsAppAction save_group_users / group_users persistence."""

from unittest.mock import AsyncMock

import pytest

from jvagent.action.whatsapp.utils.group_inbound_log import GroupInboundContext

_GROUP_ID = "120363428616636917"


class _StubWhatsAppAction:
    """Minimal stand-in for record_group_user_from_inbound behavior."""

    save_group_users = False
    group_users: dict = None
    save = AsyncMock()

    def __init__(self):
        self.group_users = {}
        self.save = AsyncMock()

    async def record_group_user_from_inbound(self, ctx):
        from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction

        await WhatsAppAction.record_group_user_from_inbound(self, ctx)


@pytest.mark.asyncio
async def test_record_group_user_upserts_user_id_to_name():
    action = _StubWhatsAppAction()
    action.save_group_users = True
    ctx = GroupInboundContext(
        group_id=_GROUP_ID,
        group_name="Team",
        sender_phone="158025151201418",
        sender_name="Staff User",
        author_raw="158025151201418",
    )
    await action.record_group_user_from_inbound(ctx)
    assert action.group_users == {_GROUP_ID: "Staff User"}
    action.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_group_user_updates_name_same_user_id():
    action = _StubWhatsAppAction()
    action.save_group_users = True
    action.group_users = {_GROUP_ID: "Old Name"}
    ctx = GroupInboundContext(
        group_id=_GROUP_ID,
        group_name="Team",
        sender_phone="158025151201418",
        sender_name="New Name",
        author_raw="158025151201418",
    )
    await action.record_group_user_from_inbound(ctx)
    assert action.group_users[_GROUP_ID] == "New Name"
    action.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_group_user_skips_when_disabled():
    action = _StubWhatsAppAction()
    action.save_group_users = False
    ctx = GroupInboundContext(
        group_id=_GROUP_ID,
        group_name="",
        sender_phone="5926178650",
        sender_name="Staff",
        author_raw="",
    )
    await action.record_group_user_from_inbound(ctx)
    assert action.group_users == {}
    action.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_group_user_skips_when_user_id_empty():
    action = _StubWhatsAppAction()
    action.save_group_users = True
    ctx = GroupInboundContext(
        group_id="",
        group_name="",
        sender_phone="158025151201418",
        sender_name="Staff",
        author_raw="123@lid",
    )
    await action.record_group_user_from_inbound(ctx)
    assert action.group_users == {}
    action.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_group_user_no_save_when_unchanged():
    action = _StubWhatsAppAction()
    action.save_group_users = True
    action.group_users = {_GROUP_ID: "Staff User"}
    ctx = GroupInboundContext(
        group_id=_GROUP_ID,
        group_name="",
        sender_phone="5926178650",
        sender_name="Staff User",
        author_raw="5926178650",
    )
    await action.record_group_user_from_inbound(ctx)
    action.save.assert_not_awaited()
