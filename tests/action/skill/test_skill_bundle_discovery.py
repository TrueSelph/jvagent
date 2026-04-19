"""Tests for Claude-style SKILL.md bundle discovery via SkillCatalog."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jvagent.action.skill.skill_catalog import SkillCatalog


@pytest.mark.asyncio
async def test_discover_skill_bundle_without_selector_returns_none(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / "agents" / "demo" / "assistant" / "skills" / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Use this process.", encoding="utf-8")

    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    catalog = await SkillCatalog.discover(
        visitor=visitor,
        skills_selector=None,
        skills_source="app",
    )
    assert catalog.is_empty


@pytest.mark.asyncio
async def test_discover_skill_bundle_with_all_selector(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / "agents" / "demo" / "assistant" / "skills" / "ops"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: ops_skill
description: Operations workflow
allowed-tools:
  - apply_patch
  - run_checks
---

Follow these steps.
""",
        encoding="utf-8",
    )

    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    catalog = await SkillCatalog.discover(
        visitor=visitor,
        skills_selector="-all",
        skills_source="app",
    )
    assert "ops_skill" in catalog.skills
    assert catalog.skills["ops_skill"]["description"] == "Operations workflow"
    assert catalog.skills["ops_skill"]["allowed_tools"] == ["apply_patch", "run_checks"]


@pytest.mark.asyncio
async def test_discover_skill_bundle_filters_by_list_and_glob(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "agents" / "demo" / "assistant" / "skills"
    for name in ("research", "code_review", "triage"):
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n\n{name} content.",
            encoding="utf-8",
        )

    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    catalog = await SkillCatalog.discover(
        visitor=visitor,
        skills_selector=["code_*", "research"],
        skills_source="app",
    )
    assert set(catalog.skills.keys()) == {"code_review", "research"}


@pytest.mark.asyncio
async def test_discover_skill_bundle_applies_denied_filters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "agents" / "demo" / "assistant" / "skills"
    for name in ("research", "code_review", "triage"):
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n\n{name} content.",
            encoding="utf-8",
        )

    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    catalog = await SkillCatalog.discover(
        visitor=visitor,
        skills_selector="-all",
        skills_source="app",
        denied_skills=["tri*", "research"],
    )
    assert set(catalog.skills.keys()) == {"code_review"}
