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
    ex = make_orchestrator(actions=[_ToolsAction(_PAY)])
    ex.lean_tool_threshold = 0
    ex.skill_only_tools = ["pay__*"]
    _wire_skills(
        monkeypatch, ex, [_doc("checkout", ["pay__charge"], always_active=True)]
    )

    tools = await ex._assemble_tools(
        make_visitor(utterance="charge me"), [], set(), None, "charge me", None, {}
    )
    out = await tools["pay__charge"].run({})
    assert "only available inside a skill" not in out


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
