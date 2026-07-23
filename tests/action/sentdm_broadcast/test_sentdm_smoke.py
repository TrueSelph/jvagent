"""Minimal smoke: sentdm_broadcast action import and instantiate."""

from jvagent.action.sentdm_broadcast.sentdm_broadcast_action import (
    SentDMBroadcastAction,
)


def test_sentdm_broadcast_action_instantiates() -> None:
    action = SentDMBroadcastAction()
    assert action.__class__.__name__ == "SentDMBroadcastAction"
