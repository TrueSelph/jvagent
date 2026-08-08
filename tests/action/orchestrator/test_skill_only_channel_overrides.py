"""Per-channel resolution of ``skill_only_tools`` (ADR-0043 + ADR-0032).

``channel_overrides.skill_only_tools`` REPLACES the action-level list on that
channel. The existing suite covers the "different list" case; these pin the
shapes an operator actually hits in the field and that a naive resolver gets
wrong:

- an **explicit empty** override means "gate nothing here" — not "fall back to
  the action-level list" (a truthiness-based resolver would get this backwards)
- an empty action-level list with a per-channel override gates *only* there
- resolution is per-turn, so alternating channels never leak state
- an unknown channel falls back to the action-level list
- the channel-resolved list — not the action-level one — drives the lean
  pre-surface candidate pool
- override keys match ``visitor.channel`` EXACTLY: ``whatsapp`` does not cover
  ``whatsapp_call``

That last one is the trap. Voice runs on its own channel string, so an override
written under ``whatsapp`` silently no-ops on a ``whatsapp_call`` turn and the
action-level list applies instead — which reads as "channel overrides are
broken" rather than as a typo.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Set, Tuple

import pytest

pytestmark = pytest.mark.asyncio


def _doc(name: str, tools: Any = (), always_active: bool = False) -> SimpleNamespace:
    """A minimal SkillDoc stand-in (only the fields the gate reads)."""
    return SimpleNamespace(
        name=name, requires_tools=tuple(tools), always_active=always_active
    )


class _ToolsAction:
    """A plain action exposing namespaced capability tools."""

    def __init__(self, names_descs: List[Tuple[str, str]]) -> None:
        self._t = [
            SimpleNamespace(name=n, description=d, call=None) for n, d in names_descs
        ]

    async def get_tools(self) -> List[Any]:
        return self._t


_PAY = [
    ("pay__charge", "Charge a saved payment method."),
    ("pay__refund", "Refund a settled charge."),
    ("kb__search", "Search the knowledge base."),
]


def _wire_skills(monkeypatch: pytest.MonkeyPatch, ex: Any, docs: List[Any]) -> None:
    """Surface ``docs`` as this agent's skills without touching the resolver."""
    monkeypatch.setattr(ex, "_discover_skills", lambda _agent: list(docs))
    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )


async def _gate_state(ex: Any, make_visitor: Any, channel: str) -> Tuple[bool, bool]:
    """Assemble a turn on *channel*; return ``(refused, visible)`` for pay__charge.

    ``refused`` is the load-bearing half — it is the gate itself, observed by
    calling the tool rather than by inspecting config.
    """
    visible: Set[str] = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me", channel=channel),
        [],
        visible,
        None,
        "charge me",
        None,
        {},
    )
    observation = await tools["pay__charge"].run({})
    return ("only available inside a skill" in observation), ("pay__charge" in visible)


async def test_empty_override_ungates_that_channel(
    monkeypatch, make_orchestrator, make_visitor
):
    """An explicit ``[]`` override gates nothing there — it does not fall back."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0  # list everything, so visibility is unambiguous
    ex.skill_only_tools = ["pay__*"]
    ex.channel_overrides = {"voice": {"skill_only_tools": []}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    assert await _gate_state(ex, make_visitor, "voice") == (False, True)
    assert await _gate_state(ex, make_visitor, "web") == (True, False)


async def test_override_gates_when_action_level_is_empty(
    monkeypatch, make_orchestrator, make_visitor
):
    """Nothing gated globally; the override gates its own channel only."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = []
    ex.channel_overrides = {"voice": {"skill_only_tools": ["pay__*"]}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    assert await _gate_state(ex, make_visitor, "voice") == (True, False)
    assert await _gate_state(ex, make_visitor, "web") == (False, True)


async def test_alternating_channels_do_not_leak_state(
    monkeypatch, make_orchestrator, make_visitor
):
    """Resolution is per-turn. A gated turn must not poison the next channel's
    surface, nor be poisoned by it — the assembled sets and the per-turn cache
    are shared machinery, so this is worth pinning rather than assuming."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = []
    ex.channel_overrides = {"voice": {"skill_only_tools": ["pay__*"]}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    first = await _gate_state(ex, make_visitor, "voice")
    between = await _gate_state(ex, make_visitor, "web")
    again = await _gate_state(ex, make_visitor, "voice")

    assert first == (True, False)
    assert between == (False, True)
    assert again == first


async def test_unknown_channel_falls_back_to_action_level(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.channel_overrides = {"voice": {"skill_only_tools": ["kb__*"]}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "kb__search"])])

    assert await _gate_state(ex, make_visitor, "telegram") == (True, False)
    assert await _gate_state(ex, make_visitor, "default") == (True, False)


async def test_override_key_must_match_the_channel_exactly(
    monkeypatch, make_orchestrator, make_visitor
):
    """``whatsapp`` does NOT cover ``whatsapp_call``.

    Voice runs on its own channel string, so an override written under the chat
    channel silently no-ops on a voice turn and the action-level list applies.
    No prefix matching, no aliasing — this is the field trap, pinned so nobody
    "fixes" it into fuzzy matching by accident.
    """
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.channel_overrides = {"whatsapp": {"skill_only_tools": []}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    # The override applies on the channel it names...
    assert await _gate_state(ex, make_visitor, "whatsapp") == (False, True)
    # ...and NOT on the adjacent voice channel, which keeps the action-level gate.
    assert await _gate_state(ex, make_visitor, "whatsapp_call") == (True, False)


async def test_channel_resolved_list_drives_the_lean_pool(
    monkeypatch, make_orchestrator, make_visitor
):
    """Under lean, gated names are excluded from the pre-surface candidate pool.
    That exclusion must follow the CHANNEL-resolved list, not the action-level
    one — otherwise a channel that ungates a tool still can't surface it."""
    many = _PAY + [
        (f"misc__t{i:02d}", f"Miscellaneous capability {i}") for i in range(20)
    ]
    ex = make_orchestrator(actions=[_ToolsAction(many)])
    ex.lean_tool_threshold = 5  # force lean on
    ex.lean_presurface_k = 3
    ex.skill_only_tools = []
    ex.channel_overrides = {"voice": {"skill_only_tools": ["pay__*"]}}
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    # Gated on voice: refused, and kept out of the lean pre-surface set.
    assert await _gate_state(ex, make_visitor, "voice") == (True, False)
    # Ungated on web: relevance ("charge me") pre-surfaces it normally.
    assert await _gate_state(ex, make_visitor, "web") == (False, True)


async def test_denied_channel_override_still_beats_the_gate(
    monkeypatch, make_orchestrator, make_visitor
):
    """Precedence holds per-channel: a channel that denies a gated tool removes
    it from the surface entirely rather than merely gating it."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.denied_tools = []
    ex.channel_overrides = {"voice": {"denied_tools": ["pay__charge"]}}
    # ``checkout`` owns BOTH gated tools, so the sibling below is a genuine
    # closed gate rather than an orphan — this test is about deny-vs-gate.
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "pay__refund"])])

    visible: Set[str] = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me", channel="voice"),
        [],
        visible,
        None,
        "charge me",
        None,
        {},
    )
    assert "pay__charge" not in tools
    assert "pay__charge" not in visible
    # The sibling gated tool is untouched by the deny and still gated.
    assert "pay__refund" in tools
    assert "only available inside a skill" in await tools["pay__refund"].run({})
