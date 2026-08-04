"""Skill-only tools (ADR-0043): a gated tool is callable only while a skill that
declares it in ``allowed-tools`` is active. Config marks WHICH tools are gated;
ownership comes from SKILL.md, so it cannot drift from the YAML."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.skill_gate import (
    build_skill_gate,
    install_skill_gate,
    skill_only_steer,
)
from jvagent.action.orchestrator.tools import SkillTool

pytestmark = pytest.mark.asyncio


def _doc(name, tools=(), always_active=False):
    """A minimal SkillDoc stand-in (duck-typed on the fields the gate reads)."""
    return SimpleNamespace(
        name=name, requires_tools=tuple(tools), always_active=always_active
    )


# --- unit: owner index ------------------------------------------------------


def test_build_skill_gate_indexes_owners():
    gate = build_skill_gate(
        {"pay__charge", "pay__refund"},
        [
            _doc("checkout", ["pay__charge", "email__send"]),
            _doc("refunds", ["pay__charge", "pay__refund"]),
            _doc("faq", ["kb__search"]),
        ],
    )
    # Only gated names are indexed; a skill's non-gated tools are ignored.
    assert gate.owners_for("pay__charge") == ("checkout", "refunds")
    assert gate.owners_for("pay__refund") == ("refunds",)
    assert gate.owners_for("email__send") == ()


def test_owners_are_deduped_preserving_order():
    gate = build_skill_gate(
        {"pay__charge"},
        [
            _doc("checkout", ["pay__charge", "pay__charge"]),  # listed twice
            _doc("refunds", ["pay__charge"]),
        ],
    )
    assert gate.owners_for("pay__charge") == ("checkout", "refunds")
    assert "checkout, refunds" in skill_only_steer(
        "pay__charge", gate.owners_for("pay__charge")
    )


def test_build_skill_gate_collects_always_active():
    gate = build_skill_gate(
        {"pay__charge"}, [_doc("checkout", ["pay__charge"], always_active=True)]
    )
    assert gate.always_on == frozenset({"checkout"})


def test_is_open_requires_an_active_owner():
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    assert gate.is_open("pay__charge", []) is False
    assert gate.is_open("pay__charge", ["faq"]) is False
    assert gate.is_open("pay__charge", ["faq", "checkout"]) is True


def test_is_open_for_always_active_owner_without_activation():
    gate = build_skill_gate(
        {"pay__charge"}, [_doc("checkout", ["pay__charge"], always_active=True)]
    )
    assert gate.is_open("pay__charge", []) is True


def test_orphan_is_never_open_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        gate = build_skill_gate({"pay__refund"}, [_doc("checkout", ["pay__charge"])])
    assert gate.is_open("pay__refund", ["checkout"]) is False
    assert "pay__refund" in caplog.text


# --- unit: guard wrapper ----------------------------------------------------


def _spy_tool(name):
    """A SkillTool whose runner records that it ran."""
    calls: list = []

    async def _run(args):
        calls.append(args)
        return "RAN"

    return SkillTool(name=name, description=f"{name} description", run=_run), calls


async def test_gated_call_refuses_and_names_the_skill():
    tool, calls = _spy_tool("pay__charge")
    tools = {"pay__charge": tool}
    activated: list = []
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__charge"}, gate, activated)

    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" in out
    assert "checkout" in out
    assert calls == []  # the real runner never ran


async def test_gated_call_runs_once_owner_is_activated():
    tool, calls = _spy_tool("pay__charge")
    tools = {"pay__charge": tool}
    activated: list = []
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__charge"}, gate, activated)

    activated.append("checkout")  # what use_skill does, mid-loop
    assert await tools["pay__charge"].run({"amount": 5}) == "RAN"
    assert calls == [{"amount": 5}]


async def test_gated_orphan_call_tells_the_model_not_to_retry():
    tool, calls = _spy_tool("pay__refund")
    tools = {"pay__refund": tool}
    gate = build_skill_gate({"pay__refund"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__refund"}, gate, [])

    out = await tools["pay__refund"].run({})
    assert "no available skill provides it" in out
    assert "do not retry" in out
    assert calls == []


async def test_open_gate_propagates_inner_exceptions():
    """The wrapper must not swallow a tool's failure into a steer string."""

    async def _boom(args):
        raise RuntimeError("inner exploded")

    tools = {"pay__charge": SkillTool("pay__charge", "Charge.", run=_boom)}
    gate = build_skill_gate({"pay__charge"}, [_doc("checkout", ["pay__charge"])])
    install_skill_gate(tools, {"pay__charge"}, gate, ["checkout"])

    with pytest.raises(RuntimeError, match="inner exploded"):
        await tools["pay__charge"].run({})


def test_install_preserves_name_description_and_terminal():
    tool = SkillTool(
        name="ia__interview", description="Run it", run=None, terminal=True
    )
    tools = {"ia__interview": tool}
    gate = build_skill_gate({"ia__interview"}, [_doc("intake", ["ia__interview"])])
    install_skill_gate(tools, {"ia__interview"}, gate, [])
    wrapped = tools["ia__interview"]
    assert wrapped.name == "ia__interview"
    assert wrapped.description == "Run it"
    # terminal must survive: gating an IA-as-tool must not change end-of-turn
    # semantics once the tool is legitimately reached.
    assert wrapped.terminal is True


# --- unit: config surface ---------------------------------------------------


def test_attribute_defaults_empty():
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    assert OrchestratorInteractAction().skill_only_tools == []


def test_config_hash_changes_with_skill_only_tools():
    from jvagent.action.orchestrator.catalog import compute_tool_surface_config_hash
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    ex = OrchestratorInteractAction()
    before = compute_tool_surface_config_hash(ex, ["A"])
    ex.skill_only_tools = ["pay__*"]
    after = compute_tool_surface_config_hash(ex, ["A"])
    assert before != after


# --- unit: catalog annotation -----------------------------------------------


async def test_find_tool_annotates_gated_hits():
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {
        "pay__charge": SkillTool("pay__charge", "Charge a saved card.", run=None),
        "pay__refund": SkillTool("pay__refund", "Refund a charge.", run=None),
        "kb__search": SkillTool("kb__search", "Search the knowledge base.", run=None),
    }
    cat = build_catalog_tools(
        all_tools,
        visible=set(),
        gated={"pay__charge": ("checkout",), "pay__refund": ()},
    )
    out = await cat["find_tool"].run({"query": ""})
    assert "pay__charge: Charge a saved card. (via skill: checkout)" in out
    assert (
        "pay__refund: Refund a charge. (not directly callable; no skill provides it)"
        in out
    )
    # An ungated tool is untouched.
    assert "kb__search: Search the knowledge base." in out
    assert "kb__search: Search the knowledge base. (" not in out


async def test_load_tool_annotates_gated_tool():
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {"pay__charge": SkillTool("pay__charge", "Charge a card.", run=None)}
    cat = build_catalog_tools(
        all_tools, visible=set(), gated={"pay__charge": ("checkout", "refunds")}
    )
    out = await cat["load_tool"].run({"name": "pay__charge"})
    assert "Charge a card." in out
    assert "(via skill: checkout, refunds)" in out


async def test_catalog_gated_defaults_to_none():
    """Existing call sites pass no ``gated`` and must be unaffected."""
    from jvagent.action.orchestrator.catalog import build_catalog_tools

    all_tools = {"kb__search": SkillTool("kb__search", "Search.", run=None)}
    cat = build_catalog_tools(all_tools, visible=set())
    assert "(via skill" not in await cat["find_tool"].run({"query": ""})
    assert "(via skill" not in await cat["load_tool"].run({"name": "kb__search"})


# --- integration: _assemble_tools ------------------------------------------


class _ToolsAction:
    """A plain action exposing namespaced capability tools (mirrors the fixture
    in test_lean_surfacing.py)."""

    def __init__(self, names_descs):
        self._t = [
            SimpleNamespace(name=n, description=d, call=None) for n, d in names_descs
        ]

    async def get_tools(self):
        return self._t


_PAY = [
    ("pay__charge", "Charge a saved payment method."),
    ("pay__refund", "Refund a settled charge."),
    ("kb__search", "Search the knowledge base."),
]


def _wire_skills(monkeypatch, ex, docs):
    """Surface ``docs`` as this agent's skills without touching the resolver."""
    monkeypatch.setattr(ex, "_discover_skills", lambda _agent: list(docs))
    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )


async def test_gated_tool_is_on_the_surface_but_not_listed(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0  # list everything, so absence is unambiguous
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "pay__refund"])])

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], visible, None, "charge me", None, {}
    )
    assert "pay__charge" in tools  # still on the surface (find_tool reaches it)
    assert "pay__charge" not in visible  # but not in the prompt
    assert "kb__search" in visible  # ungated tools unaffected


async def test_gated_dispatch_refuses_then_runs_after_activation(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    activated: list = []
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"),
        activated,
        set(),
        None,
        "charge me",
        None,
        {},
    )
    refused = await tools["pay__charge"].run({})
    assert "only available inside a skill" in refused and "checkout" in refused

    # use_skill mutates the same list the gate captured.
    await tools["use_skill"].run({"name": "checkout"})
    assert "checkout" in activated
    opened = await tools["pay__charge"].run({})
    assert "only available inside a skill" not in opened


async def test_always_active_owner_opens_the_gate_on_tick_one(
    monkeypatch, make_orchestrator, make_visitor
):
    """An always-active owner effectively un-gates its tools: callable, LISTED,
    and un-annotated. Hiding an already-open tool (or telling the model to
    use_skill for it) buys a discovery round-trip for a capability it has."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(
        monkeypatch, ex, [_doc("checkout", ["pay__charge"], always_active=True)]
    )

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], visible, None, "charge me", None, {}
    )
    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" not in out
    assert "pay__charge" in visible  # already-open: a normal callable tool
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "(via skill:" not in hit  # don't steer to use_skill for an open tool


async def test_closed_gate_is_still_hidden_and_annotated(
    monkeypatch, make_orchestrator, make_visitor
):
    """The mirror of the always-active case: with no owner active the tool is
    off the prompt AND annotated. Pins the conditional so it can't invert."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], visible, None, "charge me", None, {}
    )
    assert "pay__charge" not in visible
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "(via skill: checkout)" in hit


async def test_orphaned_gated_tool_is_locked(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    # 'checkout' declares only pay__charge — pay__refund has no owner.
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="refund me"),
        ["checkout"],
        set(),
        None,
        "refund me",
        None,
        {},
    )
    out = await tools["pay__refund"].run({})
    assert "no available skill provides it" in out


async def test_find_tool_annotation_reaches_the_model(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge"), [], set(), None, "charge", None, {}
    )
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "(via skill: checkout)" in hit


# --- integration: lean budget ----------------------------------------------

# 20 capability tools, several of them email-adjacent so a k-slot pre-surface
# has more relevant candidates than slots (mirrors ``_many`` in
# test_lean_surfacing.py).
_LEAN = [
    ("email__send", "Send an email message to a recipient."),
    ("email__draft", "Draft an email message."),
    ("email__list", "List email messages in the inbox."),
    ("email__search", "Search email messages."),
    ("calendar__create_event", "Create a calendar event/meeting."),
    ("files__read", "Read a file from disk."),
    ("files__write", "Write a file to disk."),
    ("weather__current", "Get the current weather for a city."),
] + [(f"misc__tool{i:02d}", f"Miscellaneous capability number {i}.") for i in range(12)]

_LEAN_PREFIXES = ("email", "calendar", "files", "weather", "misc")


async def test_gated_tool_does_not_consume_a_lean_presurface_slot(
    monkeypatch, make_orchestrator, make_visitor
):
    """Under lean, a gated name must not win a top-k slot and then be discarded:
    that shrinks the model's usable surface by one tool per gated name, with
    nothing promoted in its place."""

    async def _visible_longtail(skill_only):
        ex = make_orchestrator(actions=[_ToolsAction(_LEAN)])
        ex.lean_tool_threshold = 15
        ex.lean_presurface_k = 3
        ex.skill_only_tools = list(skill_only)
        _wire_skills(monkeypatch, ex, [_doc("drafting", ["email__draft"])])
        visible: set = set()
        meta: dict = {}
        await ex._assemble_tools(
            make_visitor(utterance="send an email to the team"),
            [],
            visible,
            None,
            "send an email to the team",
            None,
            meta,
        )
        assert meta["lean"] is True
        return {n for n in visible if n.startswith(_LEAN_PREFIXES)}

    baseline = await _visible_longtail([])
    assert len(baseline) == 3 and "email__draft" in baseline  # it wins a slot
    gated = await _visible_longtail(["email__draft"])
    assert "email__draft" not in gated
    assert len(gated) == len(baseline)  # the slot was reused, not lost


async def test_always_active_gated_tool_is_visible_under_lean(
    monkeypatch, make_orchestrator, make_visitor
):
    """Excluding gated names from the lean pool must not hide an OPEN one — the
    always-active pin puts it back."""
    ex = make_orchestrator(actions=[_ToolsAction(_LEAN)])
    ex.lean_tool_threshold = 15
    ex.lean_presurface_k = 2
    ex.skill_only_tools = ["weather__*"]
    _wire_skills(
        monkeypatch, ex, [_doc("forecast", ["weather__current"], always_active=True)]
    )

    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(
        make_visitor(utterance="hello"), [], visible, None, "hello", None, meta
    )
    assert meta["lean"] is True
    assert "weather__current" in visible  # open gate, pinned by its owner
    assert "misc__tool00" not in visible  # lean otherwise preserved


async def test_dead_skill_only_pattern_is_warned(
    monkeypatch, make_orchestrator, make_visitor, caplog
):
    """A glob that matches nothing is the one silent failure this feature can
    have: the operator believes a tool is gated and it is freely callable."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["paay__*"]  # typo
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    with caplog.at_level(logging.WARNING):
        await ex._assemble_tools(
            make_visitor(utterance="charge"), [], set(), None, "charge", None, {}
        )
    assert "matched no tool" in caplog.text
    assert "paay__*" in caplog.text


async def test_meta_tool_pattern_is_reported_protected_not_dead(
    monkeypatch, make_orchestrator, make_visitor, caplog
):
    """``find_tool``/``load_tool`` join ``tools`` after the glob match, so a
    pattern naming one used to be diagnosed as a dead pattern — the wrong
    message for a name that is protected, and misleading next to the correct
    one ``reply``/``use_skill`` already get."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["find_tool"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    with caplog.at_level(logging.WARNING):
        tools = await ex._assemble_tools(
            make_visitor(utterance="charge"), [], set(), None, "charge", None, {}
        )
    assert "protected tools" in caplog.text and "find_tool" in caplog.text
    assert "matched no tool" not in caplog.text
    # ...and nothing is actually gated, so no hit is annotated.
    assert "(via skill" not in await tools["find_tool"].run({"query": "charge"})


# --- integration: precedence -----------------------------------------------


async def test_denied_tools_beats_skill_only(
    monkeypatch, make_orchestrator, make_visitor
):
    """A denied tool is gone entirely — gating never sees it, find_tool can't
    return it, and no annotation is emitted."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.denied_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge", "pay__refund"])])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge"), ["checkout"], set(), None, "charge", None, {}
    )
    assert "pay__charge" not in tools
    hit = await tools["find_tool"].run({"query": "charge"})
    assert "pay__charge" not in hit
    # The other gated tool still exists and is gated.
    assert "pay__refund" in tools


async def test_pin_cannot_un_gate(monkeypatch, make_orchestrator, make_visitor):
    """A pin grants visibility, never callability — and gating wins on both."""
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 15
    ex.skill_only_tools = ["pay__charge"]
    ex.pinned_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    visible: set = set()
    tools = await ex._assemble_tools(
        make_visitor(utterance="hello"), [], visible, None, "hello", None, {}
    )
    assert "pay__charge" not in visible
    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" in out


async def test_skill_only_cannot_gate_egress_or_meta(
    monkeypatch, make_orchestrator, make_visitor, caplog
):
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[_ToolsAction(_PAY), ReplyAction()])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["reply", "find_tool", "use_skill", "pay__*"]
    _wire_skills(monkeypatch, ex, [_doc("checkout", ["pay__charge"])])

    visible: set = set()
    with caplog.at_level(logging.WARNING):
        tools = await ex._assemble_tools(
            make_visitor(utterance="hi"), [], visible, None, "hi", None, {}
        )
    # Protected names stay listed and ungated.
    assert "reply" in visible and "find_tool" in visible and "use_skill" in visible
    assert "only available inside a skill" not in await tools["reply"].run(
        {"text": "hi"}
    )
    assert "protected tools" in caplog.text
    # The non-protected match is still gated.
    assert "pay__charge" not in visible


async def test_skill_only_channel_override_replaces_the_list(
    monkeypatch, make_orchestrator, make_visitor
):
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    ex.channel_overrides = {"voice": {"skill_only_tools": ["kb__*"]}}
    _wire_skills(
        monkeypatch,
        ex,
        [_doc("checkout", ["pay__charge", "pay__refund"]), _doc("faq", ["kb__search"])],
    )

    visible_voice: set = set()
    await ex._assemble_tools(
        make_visitor(utterance="x", channel="voice"),
        [],
        visible_voice,
        None,
        "x",
        None,
        {},
    )
    assert "kb__search" not in visible_voice  # the channel's own list
    assert "pay__charge" in visible_voice  # the action-level list is REPLACED

    visible_web: set = set()
    await ex._assemble_tools(
        make_visitor(utterance="x", channel="web"), [], visible_web, None, "x", None, {}
    )
    assert "pay__charge" not in visible_web
    assert "kb__search" in visible_web


async def test_channel_blocked_owner_removes_its_tools_entirely(
    monkeypatch, make_orchestrator, make_visitor
):
    """A skill blocked on this channel already has its declared tools dropped from
    the surface by the ADR-0032 cleanup — gating never sees them, so there is no
    orphan to reason about and nothing leaks."""
    blocked = SimpleNamespace(
        name="checkout",
        requires_tools=("pay__charge",),
        always_active=False,
        allowed_channels=("web",),
        denied_channels=(),
        deny_access_directive="Not available here.",
    )
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(monkeypatch, ex, [blocked])

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge", channel="voice"),
        [],
        set(),
        None,
        "charge",
        None,
        {},
    )
    assert "pay__charge" not in tools


# --- integration: turn-lock surface invariant -------------------------------


async def test_task_lock_materialization_cannot_open_another_skills_gate(
    monkeypatch, make_orchestrator, make_visitor
):
    """The turn-lock surface materializes a locked skill's declared tools from
    raw actions, WITHOUT the gate. That is safe only because the materializer
    activates the very doc whose tools it materializes ("declaring implies
    owning"). Pin it: a locked skill must not be able to open a gated tool that
    a DIFFERENT skill owns."""
    from jvagent.action.orchestrator.skills import SkillDoc

    checkout = SkillDoc(
        name="checkout",
        description="Take payment.",
        body="Charge the card.",
        requires_tools=("pay__charge",),
    )
    # ``intake`` holds the turn-lock. It declares kb__search only, so it is NOT
    # an owner of pay__charge — but its lock_companions glob keeps pay__charge on
    # the restricted surface, so the gate wrapper is what has to say no.
    intake = SkillDoc(
        name="intake",
        description="Collect details.",
        body="Ask the questions.",
        requires_tools=("kb__search",),
        task_lock=True,
        lock_companions=("pay__*",),
    )

    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [checkout, intake])

    visitor = make_visitor(utterance="charge me")
    activated: list = []
    visible: set = set()
    tools = await ex._assemble_tools(
        visitor, activated, visible, None, "charge me", None, {}
    )

    locked_tools, locked_visible, section = await ex._apply_active_task_lock_skill(
        intake,
        [_ToolsAction(_PAY)],
        visitor,
        "charge me",
        tools,
        visible,
        activated,
        [],
        skill_docs=[checkout, intake],
    )

    # The lock activated intake (not checkout), so checkout's gate stays shut.
    assert "intake" in activated and "checkout" not in activated
    assert "ACTIVE SKILL IN PROGRESS: intake" in section
    # The companion glob keeps pay__charge reachable on the locked surface — so
    # the guard wrapper (not the restriction) is what has to refuse. Note the
    # turn-lock surface re-lists companions, so visibility alone is not the
    # protection here; callability is.
    assert "pay__charge" in locked_tools
    out = await locked_tools["pay__charge"].run({})
    assert "only available inside a skill" in out
    assert "checkout" in out
    # kb__search — intake's own, ungated — is unaffected.
    assert "kb__search" in locked_tools


async def test_task_lock_owner_opens_its_own_gated_tool(
    monkeypatch, make_orchestrator, make_visitor
):
    """The positive half: holding the turn-lock IS activation, so a locked skill
    that DOES declare the gated tool opens it — including on the surface
    assembled before the lock ran, since the gate holds ``activated`` by
    reference."""
    from jvagent.action.orchestrator.skills import SkillDoc

    checkout = SkillDoc(
        name="checkout",
        description="Take payment.",
        body="Charge the card.",
        requires_tools=("pay__charge",),
        task_lock=True,
    )

    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__charge"]
    _wire_skills(monkeypatch, ex, [checkout])

    visitor = make_visitor(utterance="charge me")
    activated: list = []
    visible: set = set()
    tools = await ex._assemble_tools(
        visitor, activated, visible, None, "charge me", None, {}
    )
    # Before the lock: no owner active, so the gate refuses.
    assert "only available inside a skill" in await tools["pay__charge"].run({})

    locked_tools, locked_visible, section = await ex._apply_active_task_lock_skill(
        checkout,
        [_ToolsAction(_PAY)],
        visitor,
        "charge me",
        tools,
        visible,
        activated,
        [],
        skill_docs=[checkout],
    )
    assert "checkout" in activated
    assert "ACTIVE SKILL IN PROGRESS: checkout" in section
    assert "pay__charge" in locked_tools and "pay__charge" in locked_visible
    assert "only available inside a skill" not in await locked_tools["pay__charge"].run(
        {}
    )
    # The pre-lock wrapper sees the activation by reference — no re-assembly.
    assert "only available inside a skill" not in await tools["pay__charge"].run({})
