"""Phase 1a — tool primitives, core tools, and progressive-disclosure catalogs."""

from __future__ import annotations

import pytest

from jvagent.action.skill_executive.catalog import (
    build_catalog_tools,
    build_skill_meta_tools,
)
from jvagent.action.skill_executive.core_tools import build_core_tools
from jvagent.action.skill_executive.skills import SkillDoc
from jvagent.action.skill_executive.tools import (
    SkillTool,
    parse_json_object,
    render_tools_section,
    wrap_action_tool,
)

pytestmark = pytest.mark.asyncio


async def test_parse_json_object_tolerant():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('noise {"a": 2} trailing') == {"a": 2}
    assert parse_json_object("not json") is None


async def test_render_tools_section_empty_and_full():
    assert "no tools" in render_tools_section([])
    out = render_tools_section([SkillTool("t", "does t", run=None)])  # type: ignore[arg-type]
    assert "- t: does t" in out


async def test_wrap_action_tool_surfaces_content():
    class _Result:
        content = "hello"

    class _Tool:
        name = "echo"
        description = "echoes"

        async def call(self, **kwargs):
            return _Result()

    st = wrap_action_tool(_Tool())
    assert st.name == "echo" and st.description == "echoes"
    assert await st.run({}) == "hello"


async def test_core_datetime_tool_runs():
    tools = build_core_tools(action=object())
    assert [t.name for t in tools] == ["get_current_datetime"]
    out = await tools[0].run({})
    assert "ISO 8601" in out and "Timezone" in out


async def test_catalog_find_and_load_promotes_visibility():
    all_tools = {
        "web_search__search": SkillTool("web_search__search", "search the web", run=None),  # type: ignore[arg-type]
        "calc__add": SkillTool("calc__add", "add numbers", run=None),  # type: ignore[arg-type]
    }
    visible: set = set()
    catalog = build_catalog_tools(all_tools, visible)

    found = await catalog["find_tool"].run({"query": "web"})
    assert "web_search__search" in found and "calc__add" not in found

    loaded = await catalog["load_tool"].run({"name": "calc__add"})
    assert "Loaded tool 'calc__add'" in loaded
    assert "calc__add" in visible  # promoted into the visible set

    miss = await catalog["load_tool"].run({"name": "nope"})
    assert "no such tool" in miss


async def test_skill_meta_tools_progressive_disclosure_and_missing_warning():
    docs = [
        SkillDoc(
            name="web_lookup",
            description="look something up",
            body="1. call web_search__search\n2. summarize",
            requires_tools=("web_search__search",),
        )
    ]
    activated: list = []
    meta = build_skill_meta_tools(docs, available_tool_names=set(), activated=activated)

    listing = await meta["find_skill"].run({})
    assert "web_lookup" in listing

    used = await meta["use_skill"].run({"name": "web_lookup"})
    assert "PROCEDURE:" in used and "web_search__search" in used
    assert "not currently available" in used  # soft-dependency warning
    assert activated == ["web_lookup"]


async def test_skill_meta_tools_empty_when_no_docs():
    assert build_skill_meta_tools([], set(), []) == {}


async def test_use_skill_surfaces_allowed_tools_into_visible():
    doc = SkillDoc(
        name="web_lookup",
        description="Look something up.",
        body="SOP body: call the tool then summarize.",
        requires_tools=("web_search__search", "missing_tool"),
    )
    visible: set = set()
    activated: list = []
    available = {"web_search__search"}  # missing_tool is NOT on the surface
    tools = build_skill_meta_tools([doc], available, activated, visible)

    out = await tools["use_skill"].run({"name": "web_lookup"})

    assert "web_search__search" in visible  # present tool surfaced for the model
    assert "missing_tool" not in visible  # absent tool not surfaced
    assert "web_lookup" in activated
    assert "Tools now callable" in out and "web_search__search" in out
    assert "not currently available" in out  # missing tool warned
    assert "SOP body" in out  # procedure delivered


async def test_use_skill_without_visible_set_is_noop_on_surface():
    doc = SkillDoc(name="s", description="d", body="b", requires_tools=("t",))
    tools = build_skill_meta_tools([doc], {"t"}, [])  # no visible set passed
    out = await tools["use_skill"].run({"name": "s"})
    assert "Activated skill 's'" in out


async def test_render_skills_section():
    from jvagent.action.skill_executive.prompts import render_skills_section

    assert "no skills available" in render_skills_section([])
    out = render_skills_section(
        [SkillDoc(name="research", description="Investigate a topic.", body="b")]
    )
    assert "- research: Investigate a topic." in out


async def test_render_identity_reads_agent(monkeypatch):
    from types import SimpleNamespace

    from jvagent.action.skill_executive.skill_executive_interact_action import (
        SkillExecutiveInteractAction,
    )

    ex = SkillExecutiveInteractAction()

    async def _agent(self):
        return SimpleNamespace(alias="Ada", role="a helpful guide")

    monkeypatch.setattr(SkillExecutiveInteractAction, "get_agent", _agent)
    assert await ex._render_identity() == "You are Ada, a helpful guide.\n\n"


async def test_render_identity_section():
    from jvagent.action.skill_executive.prompts import render_identity_section

    assert render_identity_section("", "") == ""
    assert render_identity_section("Ada", "a helpful guide").startswith(
        "You are Ada, a helpful guide."
    )
    assert render_identity_section("Ada", "").startswith("You are Ada.")
    assert render_identity_section("", "a concise assistant").startswith(
        "a concise assistant."
    )


async def test_system_prompt_lists_skills_and_priority_rule():
    from jvagent.action.skill_executive.prompts import (
        SKILL_EXECUTIVE_SYSTEM_PROMPT,
        render_identity_section,
        render_skills_section,
    )

    sp = SKILL_EXECUTIVE_SYSTEM_PROMPT.format(
        identity_section=render_identity_section("Executive Agent", "a helpful guide"),
        tools_section="- reply: ...",
        skills_section=render_skills_section(
            [SkillDoc(name="research", description="Investigate.", body="b")]
        ),
    )
    assert "You are Executive Agent, a helpful guide." in sp  # identity injected
    assert "AVAILABLE SKILLS" in sp  # skills listed inline, not just behind find_skill
    assert "Skills first" in sp  # priority rule present
    assert "research" in sp  # the concrete skill is named


async def test_use_skill_is_idempotent():
    doc = SkillDoc(
        name="research",
        description="d",
        body="FULL SOP BODY HERE",
        requires_tools=("web_search__search",),
    )
    visible: set = set()
    activated: list = []
    tools = build_skill_meta_tools([doc], {"web_search__search"}, activated, visible)

    first = await tools["use_skill"].run({"name": "research"})
    assert "FULL SOP BODY HERE" in first  # SOP delivered on first activation
    assert activated == ["research"]

    second = await tools["use_skill"].run({"name": "research"})
    assert "already active" in second.lower()  # idempotent directive
    assert "FULL SOP BODY HERE" not in second  # SOP not re-dumped
    assert activated == ["research"]  # not duplicated
    assert "web_search__search" in visible  # tools still surfaced
