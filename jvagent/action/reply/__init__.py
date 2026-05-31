"""ReplyAction — the Orchestrator-native egress voice (ADR-0014).

The agent's *mouth*: ``reply`` (thin literal publish), ``respond`` (voice text in
the agent's identity), and ``publish`` (the egress primitive). Identity lives on
the Agent node (``alias`` + ``role``); ReplyAction reads it for ``respond``.
Optional shaping (directives / extra system text) is applied only when a caller
passes it — the voice is never a coordinator. ``PersonaAction`` (Rails) is
unaffected; resolution prefers ReplyAction via ``Action.get_responder()``.
"""

from jvagent.action.reply.reply_action import ReplyAction

__all__ = [
    "ReplyAction",
]
