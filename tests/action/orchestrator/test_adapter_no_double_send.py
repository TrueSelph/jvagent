"""A turn delivers exactly one user message to a channel adapter.

The historical bug: two bus publishes per turn (reply + finalize/fallback) each
relayed by the channel adapter → duplicate. The emitted latch gates the second.
"""

from __future__ import annotations

import pytest

from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory.interaction import Interaction


class _RecordingAdapter(ChannelAdapter):
    def __init__(self):
        super().__init__("test")
        self.sends: list = []

    async def send(self, message) -> bool:
        self.sends.append(message.content)
        return True


@pytest.mark.asyncio
async def test_latch_gates_second_publish_to_one_adapter_send():
    bus = ResponseBus()
    adapter = _RecordingAdapter()
    bus._channel_adapters["test"] = adapter
    interaction = Interaction()

    async def _publish():
        await bus.publish(
            session_id="s1",
            content="answer",
            channel="test",
            interaction=interaction,
            interaction_id="i1",
            category="user",
        )

    await _publish()  # delivers + latches
    # Orchestrator gates a second emission on the latch — emulate that gate:
    if not interaction.has_emitted():
        await _publish()

    assert adapter.sends == ["answer"]
    assert interaction.has_emitted() is True
