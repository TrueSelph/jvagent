"""Interaction.emitted — the per-turn egress latch."""

from __future__ import annotations

from jvagent.memory.interaction import Interaction


def test_emitted_defaults_false_and_latches():
    i = Interaction()
    assert i.has_emitted() is False
    assert i.mark_emitted() is True  # first call latches
    assert i.has_emitted() is True
    assert i.mark_emitted() is False  # idempotent: already latched
    assert i.has_emitted() is True
