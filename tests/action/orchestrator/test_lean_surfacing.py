"""Lean tool surfacing (ADR-0018): above the threshold the long tail of
capability tools is kept off the prompt (reachable via find_tool), with a
relevance pre-surface so common turns need no discovery round-trip. At or below
the threshold every tool is listed (unchanged)."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.orchestrator.catalog import build_catalog_tools
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import SkillTool, render_tools_section


class _ToolsAction:
    """A plain (non-flow) action exposing ``n`` namespaced capability tools."""

    def __init__(self, names_descs):
        self._t = [
            SimpleNamespace(name=n, description=d, call=None) for n, d in names_descs
        ]

    async def get_tools(self):
        return self._t


def _many(n):
    # Distinct domains so relevance can pick the right ones.
    base = [
        ("email__send", "Send an email message to a recipient."),
        ("email__list", "List email messages in the inbox."),
        ("calendar__create_event", "Create a calendar event/meeting."),
        ("files__read", "Read a file from disk."),
        ("files__write", "Write a file to disk."),
        ("weather__current", "Get the current weather for a city."),
    ]
    extra = [
        (f"misc__tool{i:02d}", f"Miscellaneous capability number {i}.")
        for i in range(n - len(base))
    ]
    return base + extra


# --- unit: relevance scorer -------------------------------------------------


def test_presurface_ranks_by_overlap():
    tools = {n: SimpleNamespace(name=n, description=d) for n, d in _many(20)}
    cand = set(tools)
    keep = OrchestratorInteractAction._presurface_tools(
        "please send an email to my manager", cand, tools, k=3
    )
    assert "email__send" in keep
    assert len(keep) <= 3
    # An unrelated tool should not crowd in on a focused query.
    assert "weather__current" not in keep


def test_presurface_empty_when_no_overlap():
    tools = {n: SimpleNamespace(name=n, description=d) for n, d in _many(20)}
    keep = OrchestratorInteractAction._presurface_tools("xyzzy", set(tools), tools, k=6)
    assert keep == set()


def test_presurface_k_zero():
    tools = {n: SimpleNamespace(name=n, description=d) for n, d in _many(20)}
    assert (
        OrchestratorInteractAction._presurface_tools("email", set(tools), tools, k=0)
        == set()
    )


# --- unit: render hint + grouped find_tool ----------------------------------


def test_render_lean_hint_appended():
    tools = [SkillTool("a", "does a", run=None)]  # type: ignore[arg-type]
    full = render_tools_section(tools)
    lean = render_tools_section(tools, lean=True)
    assert "find_tool" not in full
    assert "partial list" in lean.lower() and "find_tool" in lean


async def test_find_tool_groups_by_namespace():
    all_tools = {
        "email__send": SkillTool("email__send", "Send email", run=None),
        "email__list": SkillTool("email__list", "List email", run=None),
        "files__read": SkillTool("files__read", "Read file", run=None),
    }
    cat = build_catalog_tools(all_tools, visible=set())
    out = await cat["find_tool"].run({"query": ""})
    assert "[email]" in out and "[files]" in out
    assert out.index("[email]") < out.index("email__send")


# --- integration: _assemble_tools visibility policy -------------------------


async def test_lean_engages_above_threshold(make_orchestrator, make_visitor):
    action = _ToolsAction(_many(20))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 15
    ex.lean_presurface_k = 4
    v = make_visitor(utterance="send an email to the team")
    visible: set = set()
    meta: dict = {}
    tools = await ex._assemble_tools(
        v, [], visible, None, "send an email to the team", None, meta
    )
    assert meta["lean"] is True
    # All 20 capability tools are on the full surface (findable)...
    assert (
        sum(
            1
            for n in tools
            if n.startswith(("email", "calendar", "files", "weather", "misc"))
        )
        == 20
    )
    # ...but only a few are visible (the pre-surfaced, relevant ones).
    longtail_visible = [
        n
        for n in visible
        if n.startswith(("email", "calendar", "files", "weather", "misc"))
    ]
    assert len(longtail_visible) <= 4
    assert "email__send" in visible  # relevance pre-surfaced
    assert "find_tool" in visible  # discovery is always available


async def test_full_below_threshold(make_orchestrator, make_visitor):
    action = _ToolsAction(_many(8))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 15
    v = make_visitor(utterance="hello")
    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(v, [], visible, None, "hello", None, meta)
    assert meta["lean"] is False
    # Every capability tool is visible (unchanged behaviour for small agents).
    for n, _ in _many(8):
        assert n in visible


async def test_essentials_only_with_presurface_zero(make_orchestrator, make_visitor):
    # Documented recipe: lean_presurface_k=0 (+ low threshold) → only egress/meta/
    # core are visible; every capability tool is reached via find_tool.
    action = _ToolsAction(_many(20))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 1
    ex.lean_presurface_k = 0
    v = make_visitor(utterance="hello there")
    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(v, [], visible, None, "hello there", None, meta)
    assert meta["lean"] is True
    capability_visible = [
        n
        for n in visible
        if n.startswith(("email", "calendar", "files", "weather", "misc"))
    ]
    assert capability_visible == []  # essentials only
    assert "find_tool" in visible  # discovery is the way in


async def test_threshold_zero_disables_lean(make_orchestrator, make_visitor):
    action = _ToolsAction(_many(30))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 0  # disabled → always full
    v = make_visitor(utterance="hello")
    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(v, [], visible, None, "hello", None, meta)
    assert meta["lean"] is False
    assert "misc__tool00" in visible


# --- pinned tools + always-active skills (always-visible levers) ------------


def test_match_tool_globs_unit():
    names = {"filing__create", "filing__list", "email__send", "case__open"}
    m = OrchestratorInteractAction._match_tool_globs(["filing__*", "case__open"], names)
    assert m == {"filing__create", "filing__list", "case__open"}
    assert OrchestratorInteractAction._match_tool_globs([], names) == set()
    assert OrchestratorInteractAction._match_tool_globs([""], names) == set()


async def test_pinned_tools_survive_lean(make_orchestrator, make_visitor):
    # A pinned tool stays visible under lean even with zero relevance overlap.
    action = _ToolsAction(_many(20))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 15
    ex.lean_presurface_k = 2
    ex.pinned_tools = ["weather__*"]
    v = make_visitor(utterance="hello")  # no overlap with weather
    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(v, [], visible, None, "hello", None, meta)
    assert meta["lean"] is True
    assert "weather__current" in visible  # pinned despite no relevance/lean
    # ...and the rest of the long tail is still hidden (lean preserved).
    assert "misc__tool00" not in visible


async def test_always_active_skill_pins_its_tools(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc

    action = _ToolsAction(_many(20))
    ex = make_orchestrator(actions=[action])
    ex.lean_tool_threshold = 15
    ex.lean_presurface_k = 2

    doc = SkillDoc(
        name="filing",
        description="Always-on filing SOP.",
        body="File it.",
        requires_tools=("weather__current",),  # a tool present on the surface
        always_active=True,
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [doc]
    )

    v = make_visitor(utterance="hello")  # no overlap with weather
    visible: set = set()
    meta: dict = {}
    await ex._assemble_tools(v, [], visible, None, "hello", None, meta)
    assert meta["lean"] is True
    assert "weather__current" in visible  # pinned by the always-active skill
