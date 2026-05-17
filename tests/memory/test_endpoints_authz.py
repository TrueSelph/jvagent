"""Tests for memory endpoint auth-related behavior."""

import inspect

import pytest
from fastapi.params import Query as QueryParam

from jvagent.memory.endpoints import get_my_memory


def test_get_my_memory_user_id_is_not_query_param_default():
    """Ensure user_id is not declared as a client-controlled query default."""
    sig = inspect.signature(get_my_memory)
    user_id_default = sig.parameters["user_id"].default
    assert user_id_default is None
    assert not isinstance(user_id_default, QueryParam)


def test_get_my_memory_has_current_user_param():
    """current_user must be a recognized jvspatial AUTH_INJECTED_USER_PARAMS name
    so the route wrapper passes the authenticated user object in.
    """
    sig = inspect.signature(get_my_memory)
    assert "current_user" in sig.parameters


@pytest.mark.asyncio
async def test_current_user_id_overrides_query_user_id(monkeypatch):
    """Defense-in-depth: when current_user is supplied, its id must win over
    any user_id that might leak through from the request layer."""

    class _FakeAgent:
        def __init__(self):
            self.observed_user_ids: list[str] = []

        async def get_memory(self):
            return self

        async def get_user(self, uid: str):
            self.observed_user_ids.append(uid)
            return None  # short-circuit; we only care which uid was queried

    fake = _FakeAgent()

    async def _fake_agent_get(_agent_id: str):  # noqa: ARG001
        return fake

    from jvagent.memory import endpoints as ep

    monkeypatch.setattr(ep.Agent, "get", staticmethod(_fake_agent_get))

    class _CurrentUser:
        id = "authenticated_user"

    result = await get_my_memory(
        agent_id="agent_x",
        user_id="attacker_supplied",
        current_user=_CurrentUser(),
    )
    assert result == {"memory": {}}
    assert fake.observed_user_ids == ["authenticated_user"]


@pytest.mark.asyncio
async def test_falls_back_to_user_id_when_no_current_user(monkeypatch):
    """If current_user is not injected (older auth path), user_id is used."""

    class _FakeAgent:
        def __init__(self):
            self.observed_user_ids: list[str] = []

        async def get_memory(self):
            return self

        async def get_user(self, uid: str):
            self.observed_user_ids.append(uid)
            return None

    fake = _FakeAgent()

    async def _fake_agent_get(_agent_id: str):  # noqa: ARG001
        return fake

    from jvagent.memory import endpoints as ep

    monkeypatch.setattr(ep.Agent, "get", staticmethod(_fake_agent_get))

    await get_my_memory(agent_id="agent_x", user_id="injected_uid", current_user=None)
    assert fake.observed_user_ids == ["injected_uid"]
