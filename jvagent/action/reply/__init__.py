"""ReplyAction — the Orchestrator-native single egress (ADR-0014/0025).

The agent's *mouth*: ``reply`` (thin literal publish), ``respond`` (render text in
the agent's identity), and ``publish`` (the egress primitive). Identity lives on
the Agent node (``alias`` + ``role``); ReplyAction reads it for ``respond``.
Optional shaping (directives / extra system text) is applied only when a caller
passes it — the responder is never a coordinator. ReplyAction is jvagent's single
output contract, resolved via ``Action.get_responder()`` (ADR-0025).
"""

from jvagent.action.reply.reply_action import ReplyAction

__all__ = [
    "ReplyAction",
]
