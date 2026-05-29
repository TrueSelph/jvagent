"""M4 — Executive cognition tests (ADR-0010 §2.3).

The real ``_executive_tick`` runs; only the raw router-model call is mocked
(``router_responses`` returns canned JSON). Exercises decision parsing,
center validation, the clarify fallback, and one-model-call-per-tick.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.contracts import RETURN, Result
from jvagent.action.executive.registry import Capability, CapabilityRegistry

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


async def test_respond_decision_voiced(make_executive, make_visitor, publish_log):
    ex = make_executive(
        router_responses=['{"action": "respond", "content": "Hey, how can I help?"}']
    )
    await ex.execute(make_visitor(utterance="hi"))
    assert _contents(publish_log) == ["Hey, how can I help?"]


async def test_activate_decision_runs_center(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(name="SkillsCenter", script=[RETURN(Result(content="42"))])
    ex = make_executive(
        centers={"SkillsCenter": skills},
        router_responses=[
            '{"action":"activate","center":"SkillsCenter",'
            '"intent":"compute","on_done":"voice"}'
        ],
    )
    await ex.execute(make_visitor(utterance="what is 6 times 7?"))
    assert _contents(publish_log) == ["42"]
    assert skills.call_count == 1


async def test_activate_unknown_center_clarifies(
    make_executive, make_visitor, publish_log
):
    ex = make_executive(
        router_responses=['{"action":"activate","center":"NoSuchCenter"}'],
    )
    ex.clarify_text = "could you rephrase?"
    await ex.execute(make_visitor(utterance="do the thing"))
    assert _contents(publish_log) == ["could you rephrase?"]


async def test_unparseable_decision_clarifies(
    make_executive, make_visitor, publish_log
):
    ex = make_executive(router_responses=["not json at all"])
    ex.clarify_text = "say again?"
    await ex.execute(make_visitor(utterance="???"))
    assert _contents(publish_log) == ["say again?"]


async def test_json_embedded_in_prose_is_parsed(
    make_executive, make_visitor, publish_log
):
    ex = make_executive(
        router_responses=['Sure! {"action":"respond","content":"hello"} done']
    )
    await ex.execute(make_visitor(utterance="hi"))
    assert _contents(publish_log) == ["hello"]


async def test_yield_decision(make_executive, make_visitor, publish_log):
    ex = make_executive(
        router_responses=['{"action":"yield","reason":"nothing to do"}']
    )
    await ex.execute(make_visitor(utterance="..."))
    assert publish_log == []


async def test_integrate_then_executive_frames(
    make_executive, make_visitor, stub_center, publish_log
):
    skills = stub_center(
        name="SkillsCenter", script=[RETURN(Result(content="raw data"))]
    )
    # First tick activates with integrate; second tick (after the center
    # returns into working memory) responds with a framed answer.
    ex = make_executive(
        centers={"SkillsCenter": skills},
        router_responses=[
            '{"action":"activate","center":"SkillsCenter","on_done":"integrate"}',
            '{"action":"respond","content":"Here is what I found: raw data"}',
        ],
    )
    await ex.execute(make_visitor(utterance="look it up"))
    assert _contents(publish_log) == ["Here is what I found: raw data"]
    assert skills.call_count == 1


async def test_routing_prompt_surfaces_centers_and_capabilities(make_visitor):
    # Direct unit test of prompt assembly — no loop, no model.
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.executive_interact_action import (
        ExecutiveInteractAction,
    )
    from jvagent.action.executive.state import ModelBudget, WorkingMemory

    ex = ExecutiveInteractAction()
    registry = CapabilityRegistry(
        [
            Capability(
                id="WeatherIA",
                kind="ia",
                center="IACenter",
                summary="look up the weather",
            )
        ]
    )
    visitor = make_visitor(utterance="weather?")
    visitor.conversation = None  # skip history fetch
    ctx = TurnContext(
        visitor=visitor,
        wm=WorkingMemory(),
        model_budget=ModelBudget(),
        action=ex,
        registry=registry,
        center_names=["IACenter", "SkillsCenter"],
    )
    system_prompt, user_prompt = await ex._build_routing_prompt(
        ctx, ["IACenter", "SkillsCenter"]
    )
    assert "IACenter" in system_prompt and "SkillsCenter" in system_prompt
    assert "WeatherIA" in system_prompt
    assert "look up the weather" in system_prompt
    assert "weather?" in user_prompt
