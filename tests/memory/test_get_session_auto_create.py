"""get_session creates User/Conversation when session_id or user_id is missing locally."""

import pytest

from jvagent.memory.manager import Memory


@pytest.mark.asyncio
async def test_get_session_session_only_creates_when_missing(test_db):
    memory = await Memory.create()
    session_id = "sess_client_provided_missing_01"

    user, conv, resolved_uid, resolved_sid, new_user = await memory.get_session(
        user_id=None,
        session_id=session_id,
        channel="default",
    )

    assert new_user is True
    assert resolved_sid == session_id == conv.session_id
    assert resolved_uid == user.user_id
    assert conv.user_id == user.user_id

    again = await memory.get_conversation_by_session(session_id)
    assert again is not None
    assert again.id == conv.id


@pytest.mark.asyncio
async def test_get_session_both_ids_creates_conversation_when_missing(test_db):
    memory = await Memory.create()
    user = await memory.get_user("existing_for_sess_create", create_if_missing=True)
    session_id = "sess_new_for_existing_user_01"

    out_user, conv, resolved_uid, resolved_sid, new_user = await memory.get_session(
        user_id=user.user_id,
        session_id=session_id,
        channel="default",
    )

    assert new_user is False
    assert out_user.id == user.id
    assert resolved_uid == user.user_id
    assert resolved_sid == session_id == conv.session_id
    assert conv.user_id == user.user_id


@pytest.mark.asyncio
async def test_get_session_both_ids_creates_user_when_missing(test_db):
    memory = await Memory.create()
    session_id = "sess_for_brand_new_user_01"
    external_user_id = "brand_new_external_user_01"

    user, conv, resolved_uid, resolved_sid, new_user = await memory.get_session(
        user_id=external_user_id,
        session_id=session_id,
        channel="default",
    )

    assert new_user is True
    assert resolved_uid == external_user_id == user.user_id
    assert resolved_sid == session_id == conv.session_id
    assert conv.user_id == external_user_id
