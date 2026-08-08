"""Tests for reusable skill bundle resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)


def test_resolve_builtin_skills_contains_catalog_entries() -> None:
    skills = resolve_builtin_skills()
    assert "answer" in skills
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
    # ``research`` is a built-in skill; an app-local one of the same name must win.
    skill_dir = tmp_path / "agents" / "acme" / "bot" / "skills" / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: research
description: App override for built-in
---

App override content.
""",
        encoding="utf-8",
    )
    merged = resolve_merged_skill_bundles(
        app_root=str(tmp_path), namespace="acme", agent_name="bot"
    )
    assert "research" in merged
    assert merged["research"]["source"] == "app"
    assert merged["research"]["description"] == "App override for built-in"


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
        "answer": {"name": "answer"},
        "research": {"name": "research"},
    }
    selected = apply_skill_selector(bundles, selector="-all")
    assert set(selected.keys()) == {"answer", "research"}


def test_apply_skill_selector_list_and_glob() -> None:
    bundles = {
        "answer": {"name": "answer"},
        "research": {"name": "research"},
        "triage": {"name": "triage"},
    }
    selected = apply_skill_selector(bundles, selector=["ans*", "research"])
    assert set(selected.keys()) == {"answer", "research"}


def test_apply_skill_selector_empty_selector_returns_none_exposed() -> None:
    bundles = {
        "answer": {"name": "answer"},
    }
    assert apply_skill_selector(bundles, selector=None) == {}
    assert apply_skill_selector(bundles, selector=[]) == {}
    assert apply_skill_selector(bundles, selector="") == {}


def test_apply_skill_selector_denied_filter_removes_matches() -> None:
    bundles = {
        "answer": {"name": "answer"},
        "research": {"name": "research"},
        "triage": {"name": "triage"},
    }
    selected = apply_skill_selector(
        bundles,
        selector="-all",
        denied=["tri*", "research"],
    )
    assert set(selected.keys()) == {"answer"}


# ── requires-actions parsing ──────────────────────────────────────────


def test_parse_skill_bundle_extracts_requires_actions(tmp_path: Path) -> None:
    """requires-actions frontmatter key is parsed into bundle metadata."""
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: my_skill
description: Needs an action
requires-actions:
  - GoogleCalendarAction
  - EmailAction
---

SOP content.
""",
        encoding="utf-8",
    )
    data = parse_skill_bundle(skill_dir, source="builtin")
    assert data is not None
    assert data["requires_actions"] == ["GoogleCalendarAction", "EmailAction"]


def test_parse_skill_bundle_extracts_lock_companions(tmp_path: Path) -> None:
    """lock-companions frontmatter is parsed into bundle metadata."""
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = tmp_path / "iv_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: iv_skill
description: Interview with a side FAQ allowed
task-lock: true
lock-companions:
  - faq
  - find_tool
---

SOP content.
""",
        encoding="utf-8",
    )
    data = parse_skill_bundle(skill_dir, source="builtin")
    assert data is not None
    assert data["task_lock"] is True
    assert data["lock_companions"] == ["faq", "find_tool"]


def test_parse_skill_bundle_lock_companions_defaults_empty(tmp_path: Path) -> None:
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = tmp_path / "plain_iv"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: plain_iv
description: No companions
---

SOP content.
""",
        encoding="utf-8",
    )
    data = parse_skill_bundle(skill_dir, source="builtin")
    assert data is not None
    assert data["lock_companions"] == []


def test_parse_skill_bundle_requires_actions_defaults_empty(tmp_path: Path) -> None:
    """Bundles without requires-actions get an empty list (backward compat)."""
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = tmp_path / "plain_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: plain_skill
description: No action deps
---

SOP content.
""",
        encoding="utf-8",
    )
    data = parse_skill_bundle(skill_dir, source="builtin")
    assert data is not None
    assert data["requires_actions"] == []


def test_parse_skill_bundle_requires_actions_string_form(tmp_path: Path) -> None:
    """Single-string requires-actions is normalized to a one-item list."""
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = tmp_path / "single_action"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: single_action
description: One action
requires-actions: GoogleCalendarAction
---

SOP content.
""",
        encoding="utf-8",
    )
    data = parse_skill_bundle(skill_dir, source="builtin")
    assert data is not None
    assert data["requires_actions"] == ["GoogleCalendarAction"]


class TestFrontmatterLeadingCharacters:
    """A SKILL.md whose ``---`` is not the very first byte.

    ``_parse_frontmatter`` tested ``raw.startswith("---")`` on the UNSTRIPPED
    text while using the stripped text for everything else. A leading blank
    line, indentation, or an editor-inserted UTF-8 BOM therefore made the whole
    frontmatter block read as body, which fails twice over:

    - ``allowed-tools`` is never parsed, so the skill declares no tools and
      cannot own anything ``skill_only_tools`` gates
    - the frontmatter text lands in the rendered procedure, so ``allowed-tools:``
      shows up verbatim in the prompt

    ``sop_extend.py`` already strips before the check; this is the outlier.
    """

    BODY = (
        "---\n"
        "name: doc_helper\n"
        "description: helps with docs\n"
        "allowed-tools:\n"
        "  - pageindex__search\n"
        "  - pageindex__list\n"
        "---\n"
        "\n"
        "# Procedure\n"
        "Do the thing.\n"
    )

    @pytest.mark.parametrize(
        "label,prefix",
        [
            ("leading newline", "\n"),
            ("leading blank lines", "\n\n"),
            ("leading spaces", "  "),
            ("utf-8 bom", "﻿"),
            ("bom then newline", "﻿\n"),
        ],
    )
    def test_frontmatter_is_parsed_despite_leading_characters(
        self, label: str, prefix: str
    ) -> None:
        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        meta, content = _parse_frontmatter(prefix + self.BODY, Path("SKILL.md"))
        assert meta.get("allowed-tools") == [
            "pageindex__search",
            "pageindex__list",
        ], f"{label}: allowed-tools must still be parsed"
        assert (
            "allowed-tools" not in content
        ), f"{label}: frontmatter must not leak into the rendered body"
        assert content.startswith("# Procedure"), f"{label}: body should start clean"

    def test_clean_file_still_parses(self) -> None:
        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        meta, content = _parse_frontmatter(self.BODY, Path("SKILL.md"))
        assert meta["name"] == "doc_helper"
        assert content.startswith("# Procedure")

    def test_body_horizontal_rule_is_not_a_delimiter(self) -> None:
        """``---`` as a markdown rule in the body must survive intact."""
        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        raw = self.BODY + "\nSection\n\n---\n\nMore\n"
        _meta, content = _parse_frontmatter(raw, Path("SKILL.md"))
        assert "---" in content
        assert content.rstrip().endswith("More")

    def test_file_without_frontmatter_is_untouched(self) -> None:
        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        meta, content = _parse_frontmatter("# Just a doc\n\nbody\n", Path("SKILL.md"))
        assert meta == {}
        assert content == "# Just a doc\n\nbody"


class TestFrontmatterKeySpelling:
    """Underscore spellings of hyphenated frontmatter keys.

    ``task_lock``, ``allowed_channels``, ``requires_tasks``,
    ``lock_companions`` and ``deny_access_directive`` were each given an
    explicit underscore fallback at their read site, but ``allowed_tools`` and
    the rest were not. The inconsistency is invisible: a skill written with
    ``allowed_tools:`` parses fine, loads fine, and owns no tools — the same end
    state as the BOM bug, reached a different way. Underscores are now accepted
    for every known key.
    """

    @staticmethod
    def _write(tmp_path: Path, frontmatter: str) -> Path:
        skill_dir = tmp_path / "spelling_skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\n{frontmatter}---\n\n# Procedure\nDo the thing.\n",
            encoding="utf-8",
        )
        return skill_dir

    def test_allowed_tools_underscore_is_accepted(self, tmp_path: Path) -> None:
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        skill_dir = self._write(
            tmp_path,
            "name: doc_helper\n"
            "description: helps with docs\n"
            "allowed_tools:\n"
            "  - pageindex__search\n"
            "  - pageindex__list\n",
        )
        data = parse_skill_bundle(skill_dir, source="builtin")
        assert data is not None
        assert data["allowed_tools"] == ["pageindex__search", "pageindex__list"]

    @pytest.mark.parametrize(
        "written,canonical",
        [
            ("disabled_tools", "disabled_tools"),
            ("requires_actions", "requires_actions"),
            ("coactivate_with", "coactivate_with"),
        ],
    )
    def test_other_list_keys_accept_underscores(
        self, tmp_path: Path, written: str, canonical: str
    ) -> None:
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        skill_dir = self._write(
            tmp_path,
            f"name: doc_helper\ndescription: d\n{written}:\n  - alpha\n",
        )
        data = parse_skill_bundle(skill_dir, source="builtin")
        assert data is not None
        assert data[canonical] == ["alpha"]

    @pytest.mark.parametrize(
        "block",
        [
            "allowed_tools:\n  - underscore_tool\nallowed-tools:\n  - hyphen_tool\n",
            "allowed-tools:\n  - hyphen_tool\nallowed_tools:\n  - underscore_tool\n",
        ],
        ids=["underscore-first", "hyphen-first"],
    )
    def test_hyphenated_spelling_wins_when_both_are_present(
        self, tmp_path: Path, block: str
    ) -> None:
        """A file carrying both spellings must not depend on YAML key order."""
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        skill_dir = self._write(tmp_path, f"name: doc_helper\ndescription: d\n{block}")
        data = parse_skill_bundle(skill_dir, source="builtin")
        assert data is not None
        assert data["allowed_tools"] == ["hyphen_tool"]

    def test_near_miss_key_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """A typo'd key is still ignored — but it no longer goes unmentioned."""
        import logging

        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        with caplog.at_level(logging.WARNING, logger="jvagent.scaffold.skill_resolve"):
            meta, _content = _parse_frontmatter(
                "---\nname: s\nallowed-tool:\n  - a\n---\n\nbody\n",
                Path("SKILL.md"),
            )
        assert "allowed-tool" in meta, "unknown keys are preserved, not dropped"
        assert "allowed-tools" not in meta, "a typo must not silently take effect"
        assert any(
            "allowed-tool" in record.message and "allowed-tools" in record.message
            for record in caplog.records
        ), "the near-miss should name the key it was probably meant to be"

    def test_unrelated_unknown_key_is_left_alone(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Custom keys are legal; only near-misses are worth a warning."""
        import logging

        from jvagent.scaffold.skill_resolve import _parse_frontmatter

        with caplog.at_level(logging.WARNING, logger="jvagent.scaffold.skill_resolve"):
            meta, _content = _parse_frontmatter(
                "---\nname: s\nteam_owner: platform\n---\n\nbody\n",
                Path("SKILL.md"),
            )
        assert meta["team_owner"] == "platform"
        assert not caplog.records


class TestShippedFrontmatterFixture:
    """The example app carries a deliberately malformed SKILL.md.

    The inline cases above build their own files, so they keep passing even if
    the shipped fixture is quietly normalised by an editor that strips BOMs on
    save — and then nothing exercises the real discovery path against a real
    file on disk. These assert on the shipped bytes themselves.
    """

    FIXTURE = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "jvagent_app"
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "skills"
        / "smoke_frontmatter"
    )

    def test_fixture_is_still_malformed(self) -> None:
        """Guards the guard: a stripped BOM makes this suite silently weaker."""
        raw = (self.FIXTURE / "SKILL.md").read_bytes()
        assert raw.startswith(
            b"\xef\xbb\xbf"
        ), "the UTF-8 BOM is the point of this file"
        assert b"allowed_tools:" in raw, "underscore spelling is the point of this file"
        assert b"requires_actions:" in raw

    def test_shipped_fixture_parses_through_discovery(self) -> None:
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        data = parse_skill_bundle(self.FIXTURE, source="app")
        assert data is not None
        assert data["allowed_tools"] == [
            "file_interface__list_directory",
            "file_interface__read_file",
        ]
        assert data["requires_actions"] == ["FileInterfaceAction"]
        # The body explains the bug, so it legitimately mentions "allowed-tools:".
        # Assert on tokens that only ever appear inside the frontmatter block.
        content = data["content"]
        assert content.startswith("# Frontmatter Smoke")
        assert "name: smoke_frontmatter" not in content
        assert "spec: jv" not in content
        assert "allowed_tools:\n" not in content

    def test_fixture_is_exposed_by_the_example_agent(self) -> None:
        """A fixture no agent loads is not exercising discovery at all."""
        import yaml

        agent_yaml = self.FIXTURE.parents[1] / "agent.yaml"
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
        skills: list = []
        for entry in data.get("actions") or []:
            if isinstance(entry, dict):
                skills += (entry.get("context") or {}).get("skills") or []
        assert "smoke_frontmatter" in skills
