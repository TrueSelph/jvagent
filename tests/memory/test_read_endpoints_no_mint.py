"""Read-only memory endpoints must not mint a User (AUDIT-memory LOW).

get_user defaults to create_if_missing=True; the GET endpoints must pass
create_if_missing=False so a read never creates a persistent User node."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.memory import endpoints as ep

pytestmark = pytest.mark.asyncio


def _patch_agent(monkeypatch, get_user_mock):
    memory = SimpleNamespace(get_user=get_user_mock)
    agent = SimpleNamespace(get_memory=AsyncMock(return_value=memory))
    monkeypatch.setattr(ep.Agent, "get", AsyncMock(return_value=agent))
    return get_user_mock


async def test_get_my_memory_does_not_mint_user(monkeypatch):
    get_user = _patch_agent(monkeypatch, AsyncMock(return_value=None))

    result = await ep.get_my_memory("agent1", user_id="alice")

    assert result == {"memory": {}}
    get_user.assert_awaited_once()
    assert get_user.call_args.kwargs.get("create_if_missing") is False


async def test_get_user_memory_content_does_not_mint_user(monkeypatch):
    from jvspatial.api.exceptions import ResourceNotFoundError

    get_user = _patch_agent(monkeypatch, AsyncMock(return_value=None))

    with pytest.raises(ResourceNotFoundError):
        await ep.get_user_memory_content("agent1", "bob")

    get_user.assert_awaited_once()
    assert get_user.call_args.kwargs.get("create_if_missing") is False
