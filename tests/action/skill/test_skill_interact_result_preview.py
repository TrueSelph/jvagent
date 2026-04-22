"""Tests for skill interact tool result preview formatting."""

from jvagent.action.skill.skill_action import SkillAction


def test_format_result_preview_joins_lines_with_newlines():
    preview = SkillAction._format_result_preview("alpha\nbeta\ngamma")
    assert "\n" in preview
    assert "alpha" in preview
    assert "beta" in preview
    assert "; " not in preview
    assert "more line" in preview or "more lines" in preview
