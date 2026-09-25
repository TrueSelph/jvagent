"""ADR-0055 — find_capability as primary discovery over skills + tools."""

from __future__ import annotations

from jvagent.action.orchestrator.catalog import (
    build_capability_catalog_tools,
    build_catalog_tools,
    build_skill_meta_tools,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.action.orchestrator.tools import SkillTool


def _tool(name: str, desc: str) -> SkillTool:
    async def _run(_args):
        return "ok"

    return SkillTool(name=name, description=desc, run=_run)


def _dash_skill() -> SkillDoc:
    return SkillDoc(
        name="integral_dashboards",
        description=(
            "Compose and customize app dashboards — create, adjust, add/remove "
            "widgets, charts, KPI tiles"
        ),
        body="sop",
        requires_tools=("integral_update_dashboard", "integral_list_dashboards"),
        metadata={"tags": ["dashboards", "charts", "adjust", "pie"]},
    )


async def test_find_capability_skills_section_first_with_cues() -> None:
    tools = {
        "integral_update_dashboard": _tool(
            "integral_update_dashboard",
            "Adjust / edit an existing dashboard — add widgets, charts, layout",
        ),
        "integral_list_dashboards": _tool(
            "integral_list_dashboards",
            "List dashboards on an app",
        ),
        "web_search__search": _tool("web_search__search", "Search the web"),
    }
    docs = [_dash_skill()]
    meta = build_capability_catalog_tools(tools, docs)
    assert "find_capability" in meta
    out = await meta["find_capability"].run({"query": "adjust dashboard"})
    assert "Preferred:" in out
    assert "Skills" in out
    assert out.index("Skills") < out.index("Tools")
    assert 'use_skill("integral_dashboards")' in out
    assert "integral_update_dashboard" in out
    assert 'load_tool("integral_update_dashboard")' in out


async def test_find_capability_tool_only_omits_empty_skills() -> None:
    tools = {
        "web_search__search": _tool("web_search__search", "Search the web"),
        "web_fetch__fetch": _tool("web_fetch__fetch", "Fetch a url"),
    }
    meta = build_capability_catalog_tools(tools, skill_docs=[])
    out = await meta["find_capability"].run({"query": "fetch url"})
    assert "Skills" not in out
    assert "Tools" in out
    assert "web_fetch__fetch" in out
    assert "Preferred:" not in out


async def test_find_capability_skill_only() -> None:
    docs = [_dash_skill()]
    meta = build_capability_catalog_tools({}, docs)
    out = await meta["find_capability"].run({"query": "pie chart dashboard"})
    assert "Skills" in out
    assert "integral_dashboards" in out
    # No tools on the surface — omit Tools section
    assert "Tools" not in out


async def test_find_capability_gated_tool_prefers_use_skill() -> None:
    tools = {
        "payments__charge": _tool("payments__charge", "Charge a card"),
    }
    docs = [
        SkillDoc(
            name="checkout",
            description="Checkout and payment SOP",
            body="sop",
            requires_tools=("payments__charge",),
        )
    ]
    gated = {"payments__charge": ("checkout",)}
    meta = build_capability_catalog_tools(tools, docs, gated=gated)
    out = await meta["find_capability"].run({"query": "charge card"})
    assert "(via skill: checkout)" in out
    assert 'use_skill("checkout")' in out


async def test_find_tool_alias_still_returns_tools() -> None:
    tools = {
        "integral_update_dashboard": _tool(
            "integral_update_dashboard",
            "Update dashboard layout or widgets",
        ),
    }
    visible: set[str] = set()
    cat = build_catalog_tools(tools, visible)
    out = await cat["find_tool"].run({"query": "update dashboard"})
    assert "integral_update_dashboard" in out
    assert "Prefer find_capability" in cat["find_tool"].description


async def test_find_capability_no_match_recovery() -> None:
    tools = {"web_search__search": _tool("web_search__search", "Search the web")}
    meta = build_capability_catalog_tools(tools, skill_docs=[])
    out = await meta["find_capability"].run({"query": "zzzzz-no-such-capability"})
    assert "no capability matches" in out
    assert "Do NOT repeat this search" in out


async def test_find_skill_alias_uses_token_ranking() -> None:
    docs = [_dash_skill()]
    meta = build_skill_meta_tools(docs, available_tool_names=set(), activated=[])
    out = await meta["find_skill"].run({"query": "adjust dashboard"})
    assert "integral_dashboards" in out
    assert "prefer find_capability" in meta["find_skill"].description.lower()
