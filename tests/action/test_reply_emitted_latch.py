"""ReplyAction no-bus publish latches interaction.emitted."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.action.reply.reply_action import ReplyAction
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_no_bus_publish_latches_emitted():
    action = ReplyAction()
    interaction = Interaction()
    visitor = SimpleNamespace(
        interaction=interaction, response_bus=None, session_id=None
    )
    await action.publish("hello there", visitor)
    assert interaction.has_emitted() is True
    assert interaction.response == "hello there"
