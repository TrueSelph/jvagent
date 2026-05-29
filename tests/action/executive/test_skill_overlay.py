"""Native SOP skill overlay tests (ADR-0010 / ADR-0011).

Skills are SOP overlays that reference action tools by name; they're surfaced
via find_skill / use_skill meta-tools and inject their procedure as an
observation. No real LM, no real skill files (skills injected via set_skills).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.action.executive.centers.skills_center import SkillsCenter, SkillTool
from jvagent.action.executive.contracts import ACTIVATE
from jvagent.action.executive.skills_catalog import SkillDoc, discover_skill_docs
from jvagent.action.executive.state import Frame

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


async def _noop(_args):
    return ""


async def test_meta_tools_find_and_use_with_missing_tool_warns():
    sc = SkillsCenter()
    sc.set_skills(
        [
            SkillDoc(
                name="Onboard",
                description="onboard a new client",
                body="1. crm__create_contact\n2. email__send",
                requires_tools=("crm__create_contact", "email__send"),
            )
        ]
    )
    frame = Frame(actor="SkillsCenter")
    ctx = SimpleNamespace(agent=None)
    tools = await sc._build_skill_meta_tools(ctx, frame, action_tools={})

    assert set(tools) == {"find_skill", "use_skill"}
    found = await tools["find_skill"].run({"query": "onboard"})
    assert "Onboard" in found and "onboard a new client" in found

    used = await tools["use_skill"].run({"name": "Onboard"})
    assert "PROCEDURE:" in used
    assert "crm__create_contact" in used
    assert "not currently available" in used  # both tools missing → warned
    assert frame.scratch["activated_skills"] == ["Onboard"]


async def test_use_skill_no_warning_when_tools_present():
    sc = SkillsCenter()
    sc.set_skills(
        [
            SkillDoc(
                name="Search",
                description="look something up",
                body="Call web_search__search.",
                requires_tools=("web_search__search",),
            )
        ]
    )
    frame = Frame(actor="SkillsCenter")
    ctx = SimpleNamespace(agent=None)
    action_tools = {
        "web_search__search": SkillTool("web_search__search", "search", _noop)
    }
    tools = await sc._build_skill_meta_tools(ctx, frame, action_tools)
    used = await tools["use_skill"].run({"name": "Search"})
    assert "PROCEDURE:" in used
    assert "not currently available" not in used


async def test_use_unknown_skill():
    sc = SkillsCenter()
    sc.set_skills([SkillDoc(name="A", description="d", body="b")])
    frame = Frame(actor="SkillsCenter")
    tools = await sc._build_skill_meta_tools(SimpleNamespace(agent=None), frame, {})
    assert "(no such skill: Z)" == await tools["use_skill"].run({"name": "Z"})


async def test_no_skills_means_no_meta_tools():
    sc = SkillsCenter()
    sc.set_skills([])
    frame = Frame(actor="SkillsCenter")
    tools = await sc._build_skill_meta_tools(SimpleNamespace(agent=None), frame, {})
    assert tools == {}


async def test_discover_skill_docs_no_agent():
    assert discover_skill_docs(None) == []


async def test_skill_overlay_through_executive_loop(
    make_executive, make_visitor, publish_log, monkeypatch
):
    """find_skill → use_skill → final, dispatched by the real Skills loop."""
    seq = [
        {"action": "tool", "tool": "find_skill", "args": {"query": "greet"}},
        {"action": "tool", "tool": "use_skill", "args": {"name": "GreetFlow"}},
        {"action": "final", "answer": "followed the SOP"},
    ]

    async def _call(self, ctx, task, tools, observations):
        ctx.use_model()
        return seq.pop(0) if seq else None

    monkeypatch.setattr(SkillsCenter, "_call_skill_model", _call)

    skills = SkillsCenter()
    skills.set_skills(
        [SkillDoc(name="GreetFlow", description="how to greet", body="Say hi warmly.")]
    )
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
        activation_budget=12,
    )
    await ex.execute(make_visitor(utterance="greet the user"))
    assert _contents(publish_log) == ["followed the SOP"]
