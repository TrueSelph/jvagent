"""Interaction-chain traversal must terminate and never treat self as next
(AUDIT-memory).

Equal-timestamp neighbors (coarse/fixed clock) could make get_next_interaction
return the previous node, ping-ponging _find_last_interaction's while-True
forever. A visited-set bounds the walk; self is excluded from the neighbor
pools."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction

pytestmark = pytest.mark.asyncio


class _FakeI:
    def __init__(self, iid):
        self.id = iid
        self._next = None

    async def get_next_interaction(self):
        return self._next


async def test_find_last_interaction_breaks_cycle():
    a, b = _FakeI("a"), _FakeI("b")
    a._next, b._next = b, a  # 2-cycle (ambiguous equal-timestamp ordering)

    conv = MagicMock(spec=Conversation)
    conv.id = "c1"
    conv.get_first_interaction = AsyncMock(return_value=a)

    # Must terminate (no hang) and return one of the cycle nodes.
    result = await Conversation._find_last_interaction(conv)
    assert result in (a, b)


async def test_find_last_interaction_linear_chain():
    a, b, c = _FakeI("a"), _FakeI("b"), _FakeI("c")
    a._next, b._next, c._next = b, c, None

    conv = MagicMock(spec=Conversation)
    conv.id = "c1"
    conv.get_first_interaction = AsyncMock(return_value=a)

    result = await Conversation._find_last_interaction(conv)
    assert result is c


async def test_get_next_interaction_excludes_self():
    i = MagicMock(spec=Interaction)
    i.id = "x"
    i.started_at = datetime.now(timezone.utc)
    i.conversation_id = "c"
    # The only "out" neighbor is self (a stray self-edge).
    i.nodes = AsyncMock(return_value=[i])

    result = await Interaction.get_next_interaction(i)
    assert result is None


async def test_get_previous_interaction_excludes_self():
    i = MagicMock(spec=Interaction)
    i.id = "x"
    i.started_at = datetime.now(timezone.utc)
    i.conversation_id = "c"
    i.nodes = AsyncMock(return_value=[i])

    result = await Interaction.get_previous_interaction(i)
    assert result is None
