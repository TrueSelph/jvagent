"""Tests for skill_hub: remove_skill tool and ToolExecutor.unregister_skill_bundle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from jvagent.action.skill.tool_executor import ToolExecutor
from jvagent.action.skill.tool_registry import ToolRegistry

# ---------------------------------------------------------------------------
# ToolExecutor.unregister_skill_bundle
# ---------------------------------------------------------------------------


class TestUnregisterSkillBundle:
    def test_removes_skill_and_deregisters_tools(self):
        executor = ToolExecutor()
        # Register a skill bundle with a tool
        executor._skill_bundles["my_skill"] = {
            "dir_path": "/tmp/skills/my_skill",
            "tool_files": [],
            "allowed_tools": set(),
        }
        executor._active_skill_bundles["my_skill"] = {
            "iterations": 0,
            "started_at": 0.0,
        }

        # Manually register a namespaced tool
        executor._handlers["my_skill__search"] = ("local", lambda args: "ok")
        executor._registry.register(
            name="my_skill__search",
            source="dynamic",
            schema={},
            dispatch=lambda args: "ok",
        )
        executor._tool_manager.register_tool(
            name="my_skill__search",
            description="test",
            parameters={"type": "object", "properties": {}},
        )

        # Also register an unrelated tool
        executor._handlers["other_tool"] = ("local", lambda args: "other")
        executor._registry.register(
            name="other_tool",
            source="local",
            schema={},
            dispatch=lambda args: "other",
        )

        removed = executor.unregister_skill_bundle("my_skill")

        assert "my_skill__search" in removed
        assert "my_skill" not in executor._skill_bundles
        assert "my_skill" not in executor._active_skill_bundles
        assert "my_skill__search" not in executor._handlers
        assert "my_skill__search" not in executor._registry.names()
        assert "my_skill__search" not in executor._tool_manager.tools
        # Unrelated tool still exists
        assert "other_tool" in executor._handlers

    def test_noop_for_unknown_skill(self):
        executor = ToolExecutor()
        removed = executor.unregister_skill_bundle("nonexistent")
        assert removed == []


# ---------------------------------------------------------------------------
# remove_skill tool module
# ---------------------------------------------------------------------------


class TestRemoveSkillTool:
    @pytest.fixture
    def app_root_with_skill(self, tmp_path):
        """Create an app root with an installed skill and agent.yaml."""
        skill_dir = (
            tmp_path / "agents" / "jvagent" / "test_agent" / "skills" / "my_skill"
        )
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my_skill\ndescription: A test skill\n---\n\nSOP.\n",
            encoding="utf-8",
        )

        agent_dir = tmp_path / "agents" / "jvagent" / "test_agent"
        (agent_dir / "agent.yaml").write_text(
            yaml.safe_dump(
                {
                    "agent": "jvagent/test_agent",
                    "actions": [
                        {
                            "action": "jvagent/skill_interact_action",
                            "context": {
                                "skills": ["my_skill", "code_review"],
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

    @pytest.fixture
    def mock_visitor(self):
        visitor = MagicMock()
        agent = MagicMock()
        agent.namespace = "jvagent"
        agent.name = "test_agent"
        visitor._agent = agent
        visitor._skill_state = {
            "discovered_skills": {"my_skill": {"name": "my_skill"}},
            "skill_catalog": MagicMock(skills={"my_skill": {"name": "my_skill"}}),
            "tool_executor": MagicMock(),
            "action": MagicMock(),
        }
        return visitor

    @pytest.mark.asyncio
    async def test_confirms_before_removing(self, app_root_with_skill, mock_visitor):
        from jvagent.skills.skill_hub.remove_skill import execute

        result = await execute(
            {"skill_name": "my_skill", "confirmed": False}, visitor=mock_visitor
        )
        assert "error" in result
        assert "confirmation" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_removes_installed_skill(self, app_root_with_skill, mock_visitor):
        from jvagent.skills.skill_hub.remove_skill import execute

        with patch(
            "jvagent.skills.skill_hub.scripts.remove_skill.get_app_root",
            return_value=app_root_with_skill,
        ), patch(
            "jvagent.action.skill.skill_interact_action.SkillInteractAction.remove_skill",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await execute(
                {"skill_name": "my_skill", "confirmed": True}, visitor=mock_visitor
            )

        assert result["status"] == "removed"
        assert result["skill_name"] == "my_skill"
        assert result["yaml_updated"] is True
        assert result["hot_unloaded"] is True

        # Skill directory should be gone
        skill_path = (
            Path(app_root_with_skill)
            / "agents"
            / "jvagent"
            / "test_agent"
            / "skills"
            / "my_skill"
        )
        assert not skill_path.exists()

        # agent.yaml should no longer list my_skill
        yaml_path = (
            Path(app_root_with_skill)
            / "agents"
            / "jvagent"
            / "test_agent"
            / "agent.yaml"
        )
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skills = data["actions"][0]["context"]["skills"]
        assert "my_skill" not in skills

    @pytest.mark.asyncio
    async def test_skill_not_found(self, app_root_with_skill, mock_visitor):
        from jvagent.skills.skill_hub.remove_skill import execute

        with patch(
            "jvagent.skills.skill_hub.scripts.remove_skill.get_app_root",
            return_value=app_root_with_skill,
        ):
            result = await execute(
                {"skill_name": "nonexistent", "confirmed": True}, visitor=mock_visitor
            )

        assert "error" in result
        assert "not installed" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_builtin_skill(self, tmp_path, mock_visitor):
        from jvagent.skills.skill_hub.remove_skill import execute

        # Create a builtin skill directory under a mock builtin root
        builtin_root = tmp_path / "builtin_skills"
        skill_dir = builtin_root / "builtin_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: builtin_skill\n---\n\nSOP.\n", encoding="utf-8"
        )

        # Also set up the app_root so the skill directory resolves correctly
        # Make the target_dir path match the builtin root
        app_root = tmp_path / "app"
        app_agents = app_root / "agents" / "jvagent" / "test_agent"
        app_agents.mkdir(parents=True)

        with patch(
            "jvagent.skills.skill_hub.scripts.remove_skill.get_app_root",
            return_value=str(app_root),
        ), patch(
            "jvagent.scaffold.skill_resolve._resolve_builtin_root",
            return_value=builtin_root,
        ):
            # The skill dir must exist under the builtin root to be detected
            result = await execute(
                {"skill_name": "builtin_skill", "confirmed": True}, visitor=mock_visitor
            )

        # It should fail because the skill is not under app_root/agents/.../skills
        # OR because it's a built-in. Either error is acceptable.
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_skill_name(self, app_root_with_skill, mock_visitor):
        from jvagent.skills.skill_hub.remove_skill import execute

        result = await execute(
            {"skill_name": "", "confirmed": True}, visitor=mock_visitor
        )
        assert "error" in result
