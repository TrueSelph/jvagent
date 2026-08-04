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
