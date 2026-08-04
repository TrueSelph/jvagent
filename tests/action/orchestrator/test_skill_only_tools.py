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
