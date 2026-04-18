"""Tests for Claude-style SKILL.md bundle discovery."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jvagent.action.thinking.thinking_interact_action import ThinkingInteractAction


def _make_action():
    action = MagicMock(spec=ThinkingInteractAction)
    action.skills = None
    action.denied_skills = []
    action.skills_source = "both"
    return action


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

    action = _make_action()
    discovered = await ThinkingInteractAction._discover_skill_bundles(action, visitor)
    assert discovered == {}


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

    action = _make_action()
    action.skills = "-all"
    action.skills_source = "app"
    discovered = await ThinkingInteractAction._discover_skill_bundles(action, visitor)
    assert "ops_skill" in discovered
    assert discovered["ops_skill"]["description"] == "Operations workflow"
    assert discovered["ops_skill"]["allowed_tools"] == ["apply_patch", "run_checks"]


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

    action = _make_action()
    action.skills = ["code_*", "research"]
    action.skills_source = "app"
    discovered = await ThinkingInteractAction._discover_skill_bundles(action, visitor)
    assert set(discovered.keys()) == {"code_review", "research"}


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

    action = _make_action()
    action.skills = "-all"
    action.skills_source = "app"
    action.denied_skills = ["tri*", "research"]
    discovered = await ThinkingInteractAction._discover_skill_bundles(action, visitor)
    assert set(discovered.keys()) == {"code_review"}
