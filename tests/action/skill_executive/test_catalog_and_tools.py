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
