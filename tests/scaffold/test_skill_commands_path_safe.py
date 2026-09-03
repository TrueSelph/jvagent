"""Skill CLI path containment tests (C11/C12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.cli.skill_commands import _skill_dir_for_agent


def test_skill_dir_rejects_traversal(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "jvagent" / "bot"
    agent_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="Invalid skill_name"):
        _skill_dir_for_agent(agent_dir, "../../../outside")


def test_skill_dir_stays_under_skills(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "jvagent" / "bot"
    agent_dir.mkdir(parents=True)
    dest = _skill_dir_for_agent(agent_dir, "my_skill")
    assert dest == agent_dir / "skills" / "my_skill"
    assert dest.resolve().is_relative_to((agent_dir / "skills").resolve())
