"""ReplyAction no-model fallback must not publish a lone space (AUDIT-actions LOW).

When there is no compose model AND both the explicit text and the utterance are
empty, the old `original_text or content or " "` sent a single-space message to
the user. Nothing should be published in that case."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.reply.reply_action import ReplyAction

pytestmark = pytest.mark.asyncio


def _visitor(utterance=""):
    inter = MagicMock()
    inter.response = ""
    inter.utterance = utterance
    inter.set_response = lambda x: True
    inter.save = AsyncMock()
    inter.directives = []
    inter.parameters = []
    inter.add_directive = lambda *a, **k: True
    inter.get_unexecuted_directives = lambda: []
    inter.get_unexecuted_parameters = lambda: []
    inter.set_to_executed = lambda *a, **k: None
    v = MagicMock()
    v.interaction = inter
    v.response_bus = None
    v.session_id = None
    v.stream = False
    return v


def _setup(monkeypatch):
    async def _agent(self):
        return SimpleNamespace(alias="Ada", role="a guide")

    monkeypatch.setattr(ReplyAction, "get_agent", _agent)
    monkeypatch.setattr(
        ReplyAction, "_compose_model_action", AsyncMock(return_value=None)
    )
    published: list = []

    async def _pub(self, text, visitor=None, **kw):
        published.append(text)

    monkeypatch.setattr(ReplyAction, "publish", _pub)
    return published


async def test_empty_no_model_publishes_nothing(monkeypatch):
    published = _setup(monkeypatch)
    ra = ReplyAction()
    await ra.respond(visitor=_visitor(utterance=""), text="")
    assert published == []  # no lone-space bubble


async def test_nonempty_no_model_still_publishes(monkeypatch):
    published = _setup(monkeypatch)
    ra = ReplyAction()
    await ra.respond(visitor=_visitor(utterance=""), text="hello there")
    assert published == ["hello there"]
