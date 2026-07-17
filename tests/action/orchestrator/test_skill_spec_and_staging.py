"""Two skill specs: the resolver tags each bundle (jv|claude), and use_skill's
activate_hook stages claude skills while leaving jv skills alone."""

from __future__ import annotations

from pathlib import Path

from jvagent.action.orchestrator.catalog import build_skill_meta_tools
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.scaffold.skill_resolve import parse_skill_bundle


def test_pdf_generation_is_claude_spec():
    b = parse_skill_bundle(Path("jvagent/skills/pdf_generation"), source="library")
    assert b["spec"] == "claude"


def test_research_defaults_to_jv_spec():
    b = parse_skill_bundle(Path("jvagent/skills/research"), source="library")
    assert b["spec"] == "jv"


def test_unknown_spec_falls_back_to_jv(tmp_path):
    skill = tmp_path / "weird"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: weird\ndescription: d\nspec: nonsense\n---\nbody\n"
    )
    b = parse_skill_bundle(skill, source="app")
    assert b["spec"] == "jv"


async def test_use_skill_runs_activate_hook_for_claude():
    calls = []

    async def _hook(doc):
        calls.append(doc.name)
        return f"staged {doc.name}"

    doc = SkillDoc(name="demo", description="d", body="SOP", spec="claude")
    activated: list = []
    meta = build_skill_meta_tools([doc], set(), activated, set(), activate_hook=_hook)
    out = await meta["use_skill"].run({"name": "demo"})
    assert calls == ["demo"]
    assert "staged demo" in out
    assert "Activated skill 'demo'" in out
    assert "PROCEDURE:" not in out


async def test_use_skill_hook_failure_does_not_break_activation():
    async def _hook(doc):
        raise RuntimeError("boom")

    doc = SkillDoc(name="demo", description="d", body="SOP", spec="claude")
    meta = build_skill_meta_tools([doc], set(), [], set(), activate_hook=_hook)
    out = await meta["use_skill"].run({"name": "demo"})
    assert "activation hook error" in out
    assert "Activated skill 'demo'" in out  # still activated
    assert "PROCEDURE:" not in out
