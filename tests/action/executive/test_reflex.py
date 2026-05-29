"""M2 — deterministic reflex pre-pass tests (ADR-0010 §2.5).

No model calls. The reflex resumes sustained activations, honors interrupts,
and routes anchor hits straight to the handling center — bypassing the
Executive's cognition entirely.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.contracts import RESPOND, RETURN, Result
from jvagent.action.executive.registry import Capability, CapabilityRegistry
from jvagent.action.executive.sustained import write_sustained

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


async def test_anchor_routes_directly_bypassing_executive(
    make_executive, make_visitor, stub_center, publish_log
):
    ia = stub_center(name="IACenter", script=[RETURN(Result(content="sunny"))])
    registry = CapabilityRegistry(
        [Capability(id="Weather", kind="ia", center="IACenter", anchors=("weather",))]
    )
    ex = make_executive(
        centers={"IACenter": ia},
        executive_script=[RESPOND("EXECUTIVE-SHOULD-NOT-RUN")],
        registry=registry,
    )
    await ex.execute(make_visitor(utterance="what's the weather today?"))
    assert _contents(publish_log) == ["sunny"]
    assert ia.call_count == 1


async def test_no_anchor_falls_through_to_executive(
    make_executive, make_visitor, publish_log
):
    registry = CapabilityRegistry(
        [Capability(id="Weather", kind="ia", center="IACenter", anchors=("weather",))]
    )
    ex = make_executive(executive_script=[RESPOND("hello!")], registry=registry)
    await ex.execute(make_visitor(utterance="tell me a joke"))
    assert _contents(publish_log) == ["hello!"]


async def test_regex_anchor_matches(
    make_executive, make_visitor, stub_center, publish_log
):
    ia = stub_center(name="IACenter", script=[RETURN(Result(content="booked"))])
    registry = CapabilityRegistry(
        [
            Capability(
                id="Booking",
                kind="ia",
                center="IACenter",
                anchor_patterns=(r"\bbook (a )?(table|flight)\b",),
            )
        ]
    )
    ex = make_executive(
        centers={"IACenter": ia},
        executive_script=[RESPOND("nope")],
        registry=registry,
    )
    await ex.execute(make_visitor(utterance="please book a table for two"))
    assert _contents(publish_log) == ["booked"]


async def test_anchor_to_unloaded_center_falls_through(
    make_executive, make_visitor, publish_log
):
    registry = CapabilityRegistry(
        [Capability(id="X", kind="ia", center="MissingCenter", anchors=("weather",))]
    )
    ex = make_executive(executive_script=[RESPOND("fallback")], registry=registry)
    await ex.execute(make_visitor(utterance="weather please"))
    assert _contents(publish_log) == ["fallback"]


async def test_suspended_activation_resumes(
    make_executive, make_visitor, stub_center, publish_log
):
    interview = stub_center(
        name="Interview", script=[RETURN(Result(content="Question 2?"))]
    )
    ex = make_executive(
        centers={"Interview": interview},
        executive_script=[RESPOND("EXEC-SHOULD-NOT-RUN")],
    )
    visitor = make_visitor(utterance="my answer is blue")
    await write_sustained(
        visitor.conversation, center="Interview", brief={"intent": "interview"}
    )
    await ex.execute(visitor)
    assert _contents(publish_log) == ["Question 2?"]
    assert interview.call_count == 1


async def test_interrupt_breaks_resume(
    make_executive, make_visitor, stub_center, publish_log
):
    interview = stub_center(name="Interview", script=[RETURN(Result(content="Q2"))])
    ex = make_executive(
        centers={"Interview": interview},
        executive_script=[RESPOND("cancelled, no problem")],
    )
    visitor = make_visitor(utterance="stop")
    await write_sustained(
        visitor.conversation, center="Interview", brief={"intent": "interview"}
    )
    await ex.execute(visitor)
    assert _contents(publish_log) == ["cancelled, no problem"]
    assert interview.call_count == 0  # interrupted → not resumed (generic lock)


class _FakeIA:
    """Minimal IA exposing a manifest with a ``can_interrupt`` flag."""

    def __init__(self, *, can_interrupt: bool):
        from jvagent.action.manifest import Manifest

        self._manifest = Manifest(turn_lock=True, can_interrupt=can_interrupt)

    def get_manifest(self):
        return self._manifest


def _ia_owned_sustained(visitor, ia_name):
    """Seed an IA-center-owned turn-lock naming ``ia_name`` as the lock owner."""
    return write_sustained(
        visitor.conversation,
        center="IACenter",
        brief={"intent": "", "slots": {"capability": ia_name}},
    )


async def test_noninterruptible_owner_resumes_on_interrupt(
    make_executive, make_visitor, stub_center, publish_log
):
    """A ``can_interrupt: false`` lock owner must SEE the interrupt utterance.

    "cancel the signup" must reach the IA center (so the interview's own
    classifier can terminate), not get stolen by the Executive.
    """
    ia_center = stub_center(
        name="IACenter", script=[RETURN(Result(content="Okay, cancelled."))]
    )
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[RESPOND("EXEC-SHOULD-NOT-RUN")],
    )
    ex.ia_center = "IACenter"
    ex._test_action_registry["InterviewIA"] = _FakeIA(can_interrupt=False)

    visitor = make_visitor(utterance="cancel the signup")
    await _ia_owned_sustained(visitor, "InterviewIA")
    await ex.execute(visitor)

    assert _contents(publish_log) == ["Okay, cancelled."]
    assert ia_center.call_count == 1  # interrupt forwarded INTO the IA


async def test_interruptible_owner_falls_through_on_interrupt(
    make_executive, make_visitor, stub_center, publish_log
):
    """A ``can_interrupt: true`` owner keeps the executive-level bypass."""
    ia_center = stub_center(
        name="IACenter", script=[RETURN(Result(content="SHOULD-NOT-RUN"))]
    )
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[RESPOND("cancelled, no problem")],
    )
    ex.ia_center = "IACenter"
    ex._test_action_registry["ChatIA"] = _FakeIA(can_interrupt=True)

    visitor = make_visitor(utterance="cancel")
    await _ia_owned_sustained(visitor, "ChatIA")
    await ex.execute(visitor)

    assert _contents(publish_log) == ["cancelled, no problem"]
    assert ia_center.call_count == 0  # bypass honored → executive handled it
