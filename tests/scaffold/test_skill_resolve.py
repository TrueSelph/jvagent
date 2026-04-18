"""Tests for reusable skill bundle resolution."""

from __future__ import annotations

from pathlib import Path

from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)


def test_resolve_builtin_skills_contains_catalog_entries() -> None:
    skills = resolve_builtin_skills()
    assert "code_review" in skills
    assert "research" in skills
    assert "triage" in skills


def test_resolve_agent_skills_reads_app_local_bundle(tmp_path: Path) -> None:
    skill_dir = tmp_path / "agents" / "acme" / "bot" / "skills" / "my_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: my_skill
description: My app-local skill
---

Local SOP.
""",
        encoding="utf-8",
    )
    skills = resolve_agent_skills(str(tmp_path), "acme", "bot")
    assert "my_skill" in skills
    assert skills["my_skill"]["source"] == "app"
    assert "Local SOP." in skills["my_skill"]["content"]


def test_resolve_merged_prefers_agent_skill_over_builtin(tmp_path: Path) -> None:
    skill_dir = tmp_path / "agents" / "acme" / "bot" / "skills" / "code_review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: code_review
description: App override for built-in
---

App override content.
""",
        encoding="utf-8",
    )
    merged = resolve_merged_skill_bundles(
        app_root=str(tmp_path), namespace="acme", agent_name="bot"
    )
    assert "code_review" in merged
    assert merged["code_review"]["source"] == "app"
    assert merged["code_review"]["description"] == "App override for built-in"


def test_resolve_agent_skills_skips_malformed_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "agents" / "acme" / "bot" / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: broken
description: [bad
---

Broken content.
""",
        encoding="utf-8",
    )
    skills = resolve_agent_skills(str(tmp_path), "acme", "bot")
    assert "broken" not in skills


def test_apply_skill_selector_all_returns_all() -> None:
    bundles = {
        "code_review": {"name": "code_review"},
        "research": {"name": "research"},
    }
    selected = apply_skill_selector(bundles, selector="-all")
    assert set(selected.keys()) == {"code_review", "research"}


def test_apply_skill_selector_list_and_glob() -> None:
    bundles = {
        "code_review": {"name": "code_review"},
        "research": {"name": "research"},
        "triage": {"name": "triage"},
    }
    selected = apply_skill_selector(bundles, selector=["code_*", "research"])
    assert set(selected.keys()) == {"code_review", "research"}


def test_apply_skill_selector_empty_selector_returns_none_exposed() -> None:
    bundles = {
        "code_review": {"name": "code_review"},
    }
    assert apply_skill_selector(bundles, selector=None) == {}
    assert apply_skill_selector(bundles, selector=[]) == {}
    assert apply_skill_selector(bundles, selector="") == {}


def test_apply_skill_selector_denied_filter_removes_matches() -> None:
    bundles = {
        "code_review": {"name": "code_review"},
        "research": {"name": "research"},
        "triage": {"name": "triage"},
    }
    selected = apply_skill_selector(
        bundles,
        selector="-all",
        denied=["tri*", "research"],
    )
    assert set(selected.keys()) == {"code_review"}
