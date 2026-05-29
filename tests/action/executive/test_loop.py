"""M1 — Executive control-loop + verb-dispatch unit tests (ADR-0010).

No real model calls. Exercises: verb dispatch, one-model-call-per-tick
enforcement, activation budget, AC gating, pipeline citizenship (no queue
curation / no walker-revisit), observability, and the mutual-exclusivity
validator.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.contracts import (
    ACTIVATE,
    RESPOND,
    RETURN,
    STEP,
    YIELD,
    Brief,
    Result,
)

from .conftest import make_ac, make_agent_with_ac

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


async def test_respond_is_voiced(make_executive, make_visitor, publish_log):
    ex = make_executive(executive_script=[RESPOND("hey there")])
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["hey there"]


async def test_yield_cedes_without_publishing(
    make_executive, make_visitor, publish_log
):
    visitor = make_visitor()
    ex = make_executive(executive_script=[YIELD()])
    await ex.execute(visitor)
    assert publish_log == []
    # No walker-revisit (the loop runs the whole turn in one execute()).
    visitor.prepend.assert_not_called()


async def test_activate_integrate_then_respond(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="r1"))])
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[
            ACTIVATE("Skills", brief=Brief(intent="do X"), on_done="integrate"),
            RESPOND("framed: r1"),
        ],
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["framed: r1"]
    assert skills.call_count == 1


async def test_activate_voice_direct(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="the answer"))])
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["the answer"]


async def test_step_then_return(make_executive, make_visitor, stub_center, publish_log):
    skills = stub_center(
        name="Skills",
        script=[STEP(), STEP(), RETURN(Result(content="done"))],
    )
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["done"]
    assert skills.call_count == 3


async def test_one_model_call_per_tick_aborts(
    make_executive, make_visitor, stub_center, publish_log
):
    # The center tries two model-budget acquisitions in one tick → abort.
    skills = stub_center(
        name="Skills",
        script=[RETURN(Result(content="never"))],
        double_model_call=True,
    )
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
        denied_text="halt",
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["halt"]


async def test_activation_budget_exhaustion(
    make_executive, make_visitor, stub_center, publish_log
):
    # Center never returns (always STEP) → loop bounded by activation_budget.
    skills = stub_center(name="Skills", script=[STEP()] * 50)
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
        activation_budget=3,
        denied_text="stop",
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["stop"]
    # budget 3: executive ACTIVATE tick + 2 center STEP ticks.
    assert skills.call_count == 2


async def test_unknown_center_safe_fallback(make_executive, make_visitor, publish_log):
    ex = make_executive(
        executive_script=[ACTIVATE("Nope", on_done="voice")],
        denied_text="nope-fallback",
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["nope-fallback"]


async def test_access_control_denies_center(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="secret"))])
    ac = make_ac(deny_labels={"tool:center:Skills"})
    agent = make_agent_with_ac(ac)
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
        agent=agent,
        denied_text="denied",
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["denied"]
    assert skills.call_count == 0  # never activated


async def test_access_control_allows_center(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="ok"))])
    ac = make_ac(deny_labels=set())  # enforcing but denies nothing
    agent = make_agent_with_ac(ac)
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
        agent=agent,
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["ok"]


async def test_bad_executive_verb_yields(make_executive, make_visitor, publish_log):
    # Cognition returns a center verb — invalid for the executive.
    ex = make_executive(executive_script=[STEP()])
    await ex.execute(make_visitor())
    assert publish_log == []  # no prose; turn finalized defensively


async def test_transient_ack_published(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="answer"))])
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice", ack="one sec…")],
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["one sec…", "answer"]
    assert publish_log[0]["transient"] is True


async def test_ack_suppressed_when_disabled(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="Skills", script=[RETURN(Result(content="answer"))])
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice", ack="one sec…")],
        enable_transient_ack=False,
    )
    await ex.execute(make_visitor())
    assert _contents(publish_log) == ["answer"]


async def test_center_execution_recorded(
    make_executive, make_visitor, stub_center, publish_log
):
    visitor = make_visitor()
    skills = stub_center(name="Skills", script=[RETURN(Result(content="x"))])
    ex = make_executive(
        centers={"Skills": skills},
        executive_script=[ACTIVATE("Skills", on_done="voice")],
    )
    await ex.execute(visitor)
    visitor.interaction.record_action_execution.assert_any_call("Skills")


async def test_sustained_activation_recorded(
    make_executive, make_visitor, stub_center, publish_log
):
    visitor = make_visitor()
    skills = stub_center(
        name="Interview",
        script=[RETURN(Result(content="Q1"), sustain=True)],
    )
    ex = make_executive(
        centers={"Interview": skills},
        executive_script=[ACTIVATE("Interview", on_done="voice")],
    )
    await ex.execute(visitor)
    obs = visitor.interaction.parameters["executive_observability"]
    assert obs["suspended"] is not None
    assert obs["suspended"]["center"] == "Interview"


async def test_observability_trace_persisted(make_executive, make_visitor, publish_log):
    visitor = make_visitor()
    ex = make_executive(executive_script=[RESPOND("hi")])
    await ex.execute(visitor)
    events = [
        e
        for e in visitor.interaction.observability_metrics
        if e.get("event_type") == "executive_tick"
    ]
    assert any(e["data"]["verb"] == "RESPOND" for e in events)
    assert "executive_observability" in visitor.interaction.parameters


async def test_working_memory_cleared_after_turn(
    make_executive, make_visitor, publish_log
):
    from jvagent.action.executive.contracts import WORKING_MEMORY_VISITOR_ATTR

    visitor = make_visitor()
    ex = make_executive(executive_script=[RESPOND("hi")])
    await ex.execute(visitor)
    assert not hasattr(visitor, WORKING_MEMORY_VISITOR_ATTR)
