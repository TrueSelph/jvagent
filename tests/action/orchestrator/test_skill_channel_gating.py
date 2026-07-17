"""Per-channel skill gating (ADR-0032).

A skill may declare ``allowed-channels`` / ``denied-channels`` to restrict where
it surfaces. On a non-allowed channel the orchestrator hides it from the whole
surface (skills_section, find_skill, use_skill, always-active pinning, auto-start)
and drops its per-skill custom tools (``<skill>__*``) and declared
``allowed-tools`` from the tool surface. A ``deny-access-directive`` is surfaced
in skills_section and returned from ``find_skill`` / ``use_skill`` when the
model probes a blocked skill so the model relays the message verbatim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.catalog import build_skill_meta_tools
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.prompts import render_skills_section
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.scaffold.skill_resolve import parse_skill_bundle

# ---------------------------------------------------------------------------
# Frontmatter parsing (parse_skill_bundle)
# ---------------------------------------------------------------------------


def _write_skill(
    tmp_path: Path, name: str, frontmatter: str, body: str = "SOP"
) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\n{frontmatter}---\n\n{body}\n", encoding="utf-8"
    )
    return skill_dir


def test_parse_skill_bundle_reads_hyphen_keys(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "wa_only",
        (
            "description: x\n"
            "allowed-channels:\n  - whatsapp\n"
            "denied-channels:\n  - default\n"
            "deny-access-directive: Use WhatsApp.\n"
        ),
    )
    bundle = parse_skill_bundle(skill_dir, source="app")
    assert bundle is not None
    assert bundle["allowed_channels"] == ["whatsapp"]
    assert bundle["denied_channels"] == ["default"]
    assert bundle["deny_access_directive"] == "Use WhatsApp."


def test_parse_skill_bundle_reads_underscore_keys(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "wa_only",
        (
            "description: x\n"
            "allowed_channels:\n  - whatsapp\n"
            "deny_access_directive: Use WhatsApp.\n"
        ),
    )
    bundle = parse_skill_bundle(skill_dir, source="app")
    assert bundle is not None
    assert bundle["allowed_channels"] == ["whatsapp"]
    assert bundle["denied_channels"] == []
    assert bundle["deny_access_directive"] == "Use WhatsApp."


def test_parse_skill_bundle_defaults_empty(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "plain", "description: x\n")
    bundle = parse_skill_bundle(skill_dir, source="app")
    assert bundle is not None
    assert bundle["allowed_channels"] == []
    assert bundle["denied_channels"] == []
    assert bundle["deny_access_directive"] == ""


def test_parse_skill_bundle_handles_string_value(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "wa_only",
        "description: x\nallowed-channels: whatsapp\n",
    )
    bundle = parse_skill_bundle(skill_dir, source="app")
    assert bundle is not None
    assert bundle["allowed_channels"] == ["whatsapp"]


# ---------------------------------------------------------------------------
# SkillDoc carries the fields + discover_skill_docs population
# ---------------------------------------------------------------------------


def test_skill_doc_carries_channel_fields() -> None:
    doc = SkillDoc(
        name="x",
        description="d",
        body="b",
        allowed_channels=("whatsapp",),
        denied_channels=("default",),
        deny_access_directive="Use WhatsApp.",
    )
    assert doc.allowed_channels == ("whatsapp",)
    assert doc.denied_channels == ("default",)
    assert doc.deny_access_directive == "Use WhatsApp."


def test_discover_skill_docs_populates_channel_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_skill(
        tmp_path,
        "wa_only",
        (
            "description: x\n"
            "allowed-channels:\n  - whatsapp\n"
            "deny-access-directive: Use WhatsApp.\n"
        ),
    )
    from jvagent.scaffold import skill_resolve as skill_resolve_mod

    monkeypatch.setattr(
        skill_resolve_mod,
        "resolve_merged_skill_bundles",
        lambda *a, **k: {
            "wa_only": {
                "name": "wa_only",
                "description": "x",
                "content": "SOP",
                "source": "app",
                "allowed_channels": ["whatsapp"],
                "denied_channels": [],
                "deny_access_directive": "Use WhatsApp.",
                "allowed_tools": [],
                "requires_actions": [],
            }
        },
    )
    monkeypatch.setattr("jvagent.core.app_context.get_app_root", lambda: str(tmp_path))

    class _Agent:
        namespace = "ns"
        name = "ag"

    from jvagent.action.orchestrator.skills import discover_skill_docs

    docs = discover_skill_docs(_Agent(), skills_source="app", selector="-all")
    wa = next(d for d in docs if d.name == "wa_only")
    assert wa.allowed_channels == ("whatsapp",)
    assert wa.deny_access_directive == "Use WhatsApp."


# ---------------------------------------------------------------------------
# _skill_channel_allowed logic
# ---------------------------------------------------------------------------


def _doc(
    allowed: Optional[tuple] = None,
    denied: Optional[tuple] = None,
    name: str = "x",
) -> SkillDoc:
    return SkillDoc(
        name=name,
        description="d",
        body="b",
        allowed_channels=tuple(allowed or ()),
        denied_channels=tuple(denied or ()),
    )


def test_channel_allowed_when_both_empty() -> None:
    assert OrchestratorInteractAction._skill_channel_allowed(_doc(), "default")
    assert OrchestratorInteractAction._skill_channel_allowed(_doc(), "whatsapp")


def test_channel_allowed_restricts_to_allowed() -> None:
    d = _doc(allowed=("whatsapp",))
    assert not OrchestratorInteractAction._skill_channel_allowed(d, "default")
    assert OrchestratorInteractAction._skill_channel_allowed(d, "whatsapp")


def test_channel_allowed_normalizes_web_to_default() -> None:
    d = _doc(allowed=("default",))
    assert OrchestratorInteractAction._skill_channel_allowed(d, "web")
    assert OrchestratorInteractAction._skill_channel_allowed(d, "")
    assert OrchestratorInteractAction._skill_channel_allowed(d, None)


def test_channel_allowed_denies_listed() -> None:
    d = _doc(denied=("default",))
    assert not OrchestratorInteractAction._skill_channel_allowed(d, "default")
    assert not OrchestratorInteractAction._skill_channel_allowed(d, "web")
    assert OrchestratorInteractAction._skill_channel_allowed(d, "whatsapp")


def test_channel_allowed_both_sets_subtracts() -> None:
    d = _doc(allowed=("whatsapp", "default"), denied=("default",))
    assert not OrchestratorInteractAction._skill_channel_allowed(d, "default")
    assert not OrchestratorInteractAction._skill_channel_allowed(d, "web")
    assert OrchestratorInteractAction._skill_channel_allowed(d, "whatsapp")


# ---------------------------------------------------------------------------
# render_skills_section surfaces blocked notes
# ---------------------------------------------------------------------------


def test_render_skills_section_appends_blocked_notes() -> None:
    docs = [SkillDoc(name="faq", description="FAQs", body="b")]
    section = render_skills_section(
        docs,
        blocked_notes=["quotation_interview: Please use WhatsApp to get a quotation."],
    )
    assert "- faq: FAQs" in section
    assert "Please use WhatsApp to get a quotation." in section
    assert "relay the message verbatim" in section


def test_render_skills_section_no_notes_unchanged() -> None:
    docs = [SkillDoc(name="faq", description="FAQs", body="b")]
    section = render_skills_section(docs)
    assert "relay the message verbatim" not in section


def test_render_skills_section_empty_docs_with_notes() -> None:
    section = render_skills_section([], blocked_notes=["x: Use WhatsApp."])
    assert "(no skills available" in section
    assert "Use WhatsApp." in section


# ---------------------------------------------------------------------------
# _assemble_tools filters docs, hides per-skill tools, surfaces notes
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> Any:
    from jvagent.tooling.tool import Tool

    async def _exec(**kwargs):
        return None

    return Tool(
        name=name,
        description=f"tool {name}",
        parameters_schema={"type": "object", "properties": {}},
        execute=_exec,
    )


async def test_assemble_tools_filters_blocked_skill_and_hides_its_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On web/default channel: a whatsapp-only skill is dropped from skill_docs,
    its per-skill custom tool (<skill>__*) is removed from the tool surface,
    and its deny directive lands in surface_meta['blocked_skill_notes']."""
    orch = OrchestratorInteractAction()

    wa_doc = SkillDoc(
        name="quotation_interview",
        description="quotes",
        body="b",
        allowed_channels=("whatsapp",),
        deny_access_directive="Please use WhatsApp to get a quotation.",
        requires_tools=("quotation_interview__check_extraction_status",),
    )
    faq_doc = SkillDoc(name="faq", description="FAQs", body="b")

    # Discover returns both docs; requires-actions always passes.
    monkeypatch.setattr(orch, "_discover_skills", lambda _agent: [wa_doc, faq_doc])
    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_action", AsyncMock())

    # Build a fake action surface containing the blocked skill's custom tool
    # plus a shared interview tool. The orchestrator drops the skill-specific
    # tool but keeps shared tools.
    class _FakeAction:
        def get_class_name(self) -> str:
            return "InterviewAction"

        async def get_tools(self):
            return [
                _make_tool("quotation_interview__check_extraction_status"),
                _make_tool("interview__next_field"),
            ]

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_enabled_actions",
        AsyncMock(return_value=[_FakeAction()]),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_safe_agent", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_enforce_required_actions",
        AsyncMock(side_effect=lambda d: d),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_select_code_execution_action",
        staticmethod(lambda _actions: None),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=None)
    )

    from jvagent.action.orchestrator.skill_tasks import compose_skill_activate_hooks

    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )

    visible: Set[str] = set()
    skill_docs: List[Any] = []
    surface_meta: Dict[str, Any] = {}

    visitor = MagicMock()
    visitor.channel = "web"

    tools = await orch._assemble_tools(
        visitor, [], visible, None, "give me a quote", skill_docs, surface_meta
    )

    # faq doc kept, quotation doc dropped
    names = {getattr(d, "name", "") for d in skill_docs}
    assert "faq" in names
    assert "quotation_interview" not in names
    # per-skill custom tool removed; shared interview tool retained
    assert "quotation_interview__check_extraction_status" not in tools
    assert "interview__next_field" in tools
    # directive surfaced in meta
    notes = surface_meta.get("blocked_skill_notes", [])
    assert any("Please use WhatsApp to get a quotation." in n for n in notes)
    # find_skill / use_skill relay the deny (not a silent miss)
    assert "find_skill" in tools and "use_skill" in tools
    found = await tools["find_skill"].run({"query": "quote"})
    assert "Please use WhatsApp to get a quotation." in found
    assert "verbatim" in found.lower()
    used = await tools["use_skill"].run({"name": "quotation_interview"})
    assert "Please use WhatsApp to get a quotation." in used
    assert "Activated skill" not in used


async def test_assemble_tools_keeps_skill_on_allowed_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On whatsapp channel the same skill is present and its tool stays."""
    orch = OrchestratorInteractAction()

    wa_doc = SkillDoc(
        name="quotation_interview",
        description="quotes",
        body="b",
        allowed_channels=("whatsapp",),
        deny_access_directive="Please use WhatsApp to get a quotation.",
        requires_tools=("quotation_interview__check_extraction_status",),
    )
    monkeypatch.setattr(orch, "_discover_skills", lambda _agent: [wa_doc])
    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_action", AsyncMock())

    class _FakeAction:
        def get_class_name(self) -> str:
            return "InterviewAction"

        async def get_tools(self):
            return [_make_tool("quotation_interview__check_extraction_status")]

    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_enabled_actions",
        AsyncMock(return_value=[_FakeAction()]),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "_safe_agent", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_enforce_required_actions",
        AsyncMock(side_effect=lambda d: d),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_select_code_execution_action",
        staticmethod(lambda _actions: None),
    )
    monkeypatch.setattr(
        OrchestratorInteractAction, "get_responder", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "jvagent.action.orchestrator.skill_tasks.compose_skill_activate_hooks",
        lambda *a, **k: (None, None),
    )

    visible: Set[str] = set()
    skill_docs: List[Any] = []
    surface_meta: Dict[str, Any] = {}
    visitor = MagicMock()
    visitor.channel = "whatsapp"

    tools = await orch._assemble_tools(
        visitor, [], visible, None, "give me a quote", skill_docs, surface_meta
    )

    assert {getattr(d, "name", "") for d in skill_docs} == {"quotation_interview"}
    assert "quotation_interview__check_extraction_status" in tools
    assert surface_meta.get("blocked_skill_notes", []) == []
    # Allowed channel: find/use still activate normally (no deny)
    found = await tools["find_skill"].run({"query": "quote"})
    assert "quotation_interview" in found
    assert "Please use WhatsApp to get a quotation." not in found
    used = await tools["use_skill"].run({"name": "quotation_interview"})
    assert "Activated skill 'quotation_interview'" in used


# ---------------------------------------------------------------------------
# find_skill / use_skill deny relay (unit)
# ---------------------------------------------------------------------------


async def test_find_skill_returns_deny_for_blocked_query_match() -> None:
    """A query matching a channel-blocked skill returns its deny directive."""
    allowed = SkillDoc(name="faq", description="policy questions", body="b")
    blocked = SkillDoc(
        name="quotation_interview",
        description="Extract product details and create quotations",
        body="b",
        allowed_channels=("whatsapp",),
        deny_access_directive="You'll need WhatsApp for a quote.",
        metadata={"tags": ["quotation", "quote", "product"]},
    )
    meta = build_skill_meta_tools([allowed], set(), [], blocked_docs=[blocked])
    out = await meta["find_skill"].run({"query": "quote"})
    assert "You'll need WhatsApp for a quote." in out
    assert "verbatim" in out.lower()
    assert "faq" not in out  # blocked match wins over listing allowed skills


async def test_find_skill_matches_blocked_skill_by_tag() -> None:
    blocked = SkillDoc(
        name="quotation_interview",
        description="create quotations",
        body="b",
        deny_access_directive="Use WhatsApp for quotes.",
        metadata={"tags": ["e-commerce", "pricing"]},
    )
    meta = build_skill_meta_tools([], set(), [], blocked_docs=[blocked])
    out = await meta["find_skill"].run({"query": "pricing"})
    assert "Use WhatsApp for quotes." in out


async def test_use_skill_returns_deny_for_blocked_name() -> None:
    blocked = SkillDoc(
        name="quotation_interview",
        description="quotes",
        body="b",
        deny_access_directive="You'll need WhatsApp for a quote.",
    )
    activated: List[str] = []
    meta = build_skill_meta_tools(
        [SkillDoc(name="faq", description="faq", body="b")],
        set(),
        activated,
        blocked_docs=[blocked],
    )
    out = await meta["use_skill"].run({"name": "quotation_interview"})
    assert "You'll need WhatsApp for a quote." in out
    assert activated == []  # must not activate a blocked skill


async def test_build_skill_meta_tools_empty_without_docs_or_blocked() -> None:
    assert build_skill_meta_tools([], set(), []) == {}
    assert build_skill_meta_tools([], set(), [], blocked_docs=[]) == {}


# ---------------------------------------------------------------------------
# Fixture-driven end-to-end: real zoon-ai skill folders
# ---------------------------------------------------------------------------


def test_real_quotation_and_pre_alert_hidden_on_default_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real quotation_interview and pre_alert_interview skills are gated
    to whatsapp via their SKILL.md frontmatter; on web/default they are hidden
    and their deny directives surface in skills_section."""
    skills_root = Path(
        "/Users/tharickjairam/jvsproject/agents/zoon-ai/agents/zoon/zoon_ai/skills"
    )
    if not (skills_root / "quotation_interview" / "SKILL.md").is_file():
        pytest.skip("zoon-ai skills not available in this checkout")

    q = parse_skill_bundle(skills_root / "quotation_interview", source="app")
    p = parse_skill_bundle(skills_root / "pre_alert_interview", source="app")
    assert q["allowed_channels"] == ["whatsapp"]
    assert p["allowed_channels"] == ["whatsapp"]
    assert "WhatsApp" in q["deny_access_directive"]
    assert "WhatsApp" in p["deny_access_directive"]

    # Gate on default channel hides both.
    q_doc = SkillDoc(
        name=q["name"],
        description=q["description"],
        body=q["content"],
        allowed_channels=tuple(q["allowed_channels"]),
        deny_access_directive=q["deny_access_directive"],
    )
    p_doc = SkillDoc(
        name=p["name"],
        description=p["description"],
        body=p["content"],
        allowed_channels=tuple(p["allowed_channels"]),
        deny_access_directive=p["deny_access_directive"],
    )
    assert not OrchestratorInteractAction._skill_channel_allowed(q_doc, "web")
    assert not OrchestratorInteractAction._skill_channel_allowed(p_doc, "default")
    assert OrchestratorInteractAction._skill_channel_allowed(q_doc, "whatsapp")
    assert OrchestratorInteractAction._skill_channel_allowed(p_doc, "whatsapp")

    section = render_skills_section(
        [],
        blocked_notes=[
            f"{q_doc.name}: {q_doc.deny_access_directive}",
            f"{p_doc.name}: {p_doc.deny_access_directive}",
        ],
    )
    assert "You'll need WhatsApp for a quote" in section
    assert "You'll need WhatsApp to check a tracking number" in section
