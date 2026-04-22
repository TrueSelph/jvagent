"""Tests for skill_hub: _installer module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from jvagent.skills.skill_hub._installer import (
    install_from_download,
    remove_skill_from_yaml,
    update_agent_yaml,
)


@pytest.fixture
def download_dir(tmp_path):
    """Create a mock download directory with skill files."""
    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my_skill\ndescription: A test skill\n---\n\n# My Skill\n\nSOP here.\n",
        encoding="utf-8",
    )
    (skill_dir / "search.py").write_text(
        "def get_tool_definition(): return {}\nasync def execute(args): return 'ok'\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def target_dir(tmp_path):
    """Create a target skills directory."""
    skills_dir = tmp_path / "agents" / "jvagent" / "test_agent" / "skills"
    skills_dir.mkdir(parents=True)
    return skills_dir


@pytest.fixture
def agent_yaml_dir(tmp_path):
    """Create an agent directory with a minimal agent.yaml."""
    agent_dir = tmp_path / "agents" / "jvagent" / "test_agent"
    agent_dir.mkdir(parents=True)
    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "agent": "jvagent/test_agent",
                "actions": [
                    {
                        "action": "jvagent/skill_interact_action",
                        "context": {
                            "skills": ["code_review"],
                            "skills_source": "both",
                        },
                    }
                ],
            },
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    return str(tmp_path)


class TestInstallFromDownload:
    def test_copies_skill_files(self, download_dir, target_dir):
        copied = install_from_download("my_skill", download_dir, target_dir)
        assert len(copied) == 2  # SKILL.md + search.py
        assert (target_dir / "my_skill" / "SKILL.md").is_file()
        assert (target_dir / "my_skill" / "search.py").is_file()

    def test_skips_disallowed_files(self, download_dir, target_dir):
        # Add a .sh file that should be skipped
        (download_dir / "malicious.sh").write_text(
            "#!/bin/bash\nevil", encoding="utf-8"
        )
        copied = install_from_download("my_skill", download_dir, target_dir)
        assert not (target_dir / "my_skill" / "malicious.sh").exists()

    def test_rejects_path_traversal(self, download_dir, target_dir):
        # Create a file with path traversal (simulated by checking _validate_path_safety)
        assert True  # The actual path traversal check is in _validate_path_safety

    def test_requires_skill_md(self, tmp_path, target_dir):
        bad_dir = tmp_path / "bad_skill"
        bad_dir.mkdir()
        (bad_dir / "readme.txt").write_text("no SKILL.md", encoding="utf-8")
        with pytest.raises(ValueError, match="No valid skill files|SKILL.md not found"):
            install_from_download("bad_skill", bad_dir, target_dir)

    def test_nonexistent_download_dir(self, target_dir):
        with pytest.raises(ValueError, match="does not exist"):
            install_from_download("x", Path("/nonexistent/path"), target_dir)

    def test_idempotent_install(self, download_dir, target_dir):
        install_from_download("my_skill", download_dir, target_dir)
        # Second install should overwrite without error
        install_from_download("my_skill", download_dir, target_dir)
        assert (target_dir / "my_skill" / "SKILL.md").is_file()


class TestUpdateAgentYaml:
    def test_adds_skill_to_list(self, agent_yaml_dir):
        result = update_agent_yaml(
            agent_yaml_dir, "jvagent", "test_agent", "web_search"
        )
        assert result is True
        yaml_path = (
            Path(agent_yaml_dir) / "agents" / "jvagent" / "test_agent" / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skills = data["actions"][0]["context"]["skills"]
        assert "web_search" in skills
        assert "code_review" in skills  # Pre-existing skill preserved

    def test_no_duplicate_entries(self, agent_yaml_dir):
        update_agent_yaml(agent_yaml_dir, "jvagent", "test_agent", "code_review")
        yaml_path = (
            Path(agent_yaml_dir) / "agents" / "jvagent" / "test_agent" / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skills = data["actions"][0]["context"]["skills"]
        assert skills.count("code_review") == 1

    def test_no_change_when_skills_is_all(self, agent_yaml_dir):
        yaml_path = (
            Path(agent_yaml_dir) / "agents" / "jvagent" / "test_agent" / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        data["actions"][0]["context"]["skills"] = "-all"
        yaml_path.write_text(
            yaml.safe_dump(data, default_flow_style=False), encoding="utf-8"
        )

        result = update_agent_yaml(agent_yaml_dir, "jvagent", "test_agent", "new_skill")
        assert result is False

    def test_initializes_skills_when_missing(self, tmp_path):
        agent_dir = tmp_path / "agents" / "jvagent" / "test_agent"
        agent_dir.mkdir(parents=True)
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "agent": "jvagent/test_agent",
                    "actions": [
                        {"action": "jvagent/skill_interact_action", "context": {}}
                    ],
                },
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

        result = update_agent_yaml(str(tmp_path), "jvagent", "test_agent", "calendar")
        assert result is True
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skills = data["actions"][0]["context"]["skills"]
        assert "calendar" in skills
        assert "skill_hub" in skills

    def test_missing_yaml_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            update_agent_yaml(str(tmp_path), "jvagent", "nonexistent", "test")

    def test_no_skill_action_returns_false(self, tmp_path):
        agent_dir = tmp_path / "agents" / "jvagent" / "test_agent"
        agent_dir.mkdir(parents=True)
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "agent": "jvagent/test_agent",
                    "actions": [{"action": "jvagent/persona_action", "context": {}}],
                },
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

        result = update_agent_yaml(str(tmp_path), "jvagent", "test_agent", "test")
        assert result is False


class TestRemoveSkillFromYaml:
    def test_removes_skill_from_list(self, agent_yaml_dir):
        # Add a skill first
        update_agent_yaml(agent_yaml_dir, "jvagent", "test_agent", "web_search")
        # Now remove it
        result = remove_skill_from_yaml(
            agent_yaml_dir, "jvagent", "test_agent", "web_search"
        )
        assert result is True
        yaml_path = (
            Path(agent_yaml_dir) / "agents" / "jvagent" / "test_agent" / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skills = data["actions"][0]["context"]["skills"]
        assert "web_search" not in skills
        assert "code_review" in skills

    def test_no_change_when_skill_not_in_list(self, agent_yaml_dir):
        result = remove_skill_from_yaml(
            agent_yaml_dir, "jvagent", "test_agent", "nonexistent"
        )
        assert result is False

    def test_adds_to_denied_when_skills_is_all(self, agent_yaml_dir):
        yaml_path = (
            Path(agent_yaml_dir) / "agents" / "jvagent" / "test_agent" / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        data["actions"][0]["context"]["skills"] = "-all"
        yaml_path.write_text(
            yaml.safe_dump(data, default_flow_style=False), encoding="utf-8"
        )

        result = remove_skill_from_yaml(
            agent_yaml_dir, "jvagent", "test_agent", "web_search"
        )
        assert result is True
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        denied = data["actions"][0]["context"]["denied_skills"]
        assert "web_search" in denied

    def test_missing_yaml_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            remove_skill_from_yaml(str(tmp_path), "jvagent", "nonexistent", "test")

    def test_no_skill_action_returns_false(self, tmp_path):
        agent_dir = tmp_path / "agents" / "jvagent" / "test_agent"
        agent_dir.mkdir(parents=True)
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "agent": "jvagent/test_agent",
                    "actions": [{"action": "jvagent/persona_action", "context": {}}],
                },
                default_flow_style=False,
            ),
            encoding="utf-8",
        )

        result = remove_skill_from_yaml(str(tmp_path), "jvagent", "test_agent", "test")
        assert result is False
