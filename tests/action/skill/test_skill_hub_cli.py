"""Tests for skill_hub: _skills_cli subprocess wrapper."""

from __future__ import annotations

import pytest

from jvagent.skills.skill_hub._skills_cli import (
    _strip_ansi,
    parse_add_list_output,
    parse_add_output,
    parse_find_output,
)


class TestStripAnsi:
    def test_removes_color_codes(self):
        text = "\x1b[38;5;145mvercel-labs/agent-skills@find-skills\x1b[0m"
        assert _strip_ansi(text) == "vercel-labs/agent-skills@find-skills"

    def test_removes_cursor_codes(self):
        text = "\x1b[?25l\x1b[?25h\x1b[999D\x1b[J"
        assert _strip_ansi(text) == ""

    def test_preserves_plain_text(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_removes_braille(self):
        text = "abc\u2815def"
        assert _strip_ansi(text) == "abcdef"

    def test_collapses_whitespace(self):
        text = "hello   world\n  foo"
        assert _strip_ansi(text) == "hello world\n  foo"


class TestParseFindOutput:
    def test_parses_find_results(self):
        raw = (
            "Install with npx skills add <owner/repo@skill>\n\n"
            "vercel-labs/agent-skills@find-skills 100.5K installs\n"
            "└ https://skills.sh/vercel-labs/agent-skills/find-skills\n\n"
            "anthropics/skills@frontend-design 50.2K installs\n"
            "└ https://skills.sh/anthropics/skills/frontend-design\n"
        )
        results = parse_find_output(raw)
        assert len(results) == 2
        assert results[0]["name"] == "find-skills"
        assert results[0]["source"] == "vercel-labs/agent-skills"
        assert results[0]["install_count"] == "100.5K"
        assert (
            results[0]["url"]
            == "https://skills.sh/vercel-labs/agent-skills/find-skills"
        )
        assert results[1]["name"] == "frontend-design"
        assert results[1]["source"] == "anthropics/skills"

    def test_empty_output(self):
        assert parse_find_output("") == []

    def test_no_matches(self):
        assert parse_find_output("some random text without skills") == []


class TestParseAddListOutput:
    def test_parses_available_skills(self):
        raw = (
            "Available Skills\n"
            "  find-skills\n"
            "    Helps users discover and install agent skills\n"
            "  frontend-design\n"
            "    Frontend design guidelines\n"
        )
        results = parse_add_list_output(raw)
        assert len(results) == 2
        assert results[0]["name"] == "find-skills"
        assert "discover" in results[0]["description"]
        assert results[1]["name"] == "frontend-design"

    def test_empty_output(self):
        assert parse_add_list_output("") == []

    def test_no_available_skills_section(self):
        assert parse_add_list_output("Some other output") == []


class TestParseAddOutput:
    def test_parses_installed_paths(self):
        raw = (
            "Copied SKILL.md to .claude/skills/find-skills/SKILL.md\n"
            "Created .claude/skills/find-skills/search.py\n"
        )
        paths = parse_add_output(raw)
        assert len(paths) >= 1

    def test_empty_output(self):
        assert parse_add_output("") == []
