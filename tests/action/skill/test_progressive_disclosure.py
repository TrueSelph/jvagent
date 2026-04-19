"""Tests for progressive skill tool activation."""

import pytest

from jvagent.action.skill.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_skill_tools_hidden_until_activation(tmp_path):
    executor = ToolExecutor(validate_calls=False)
    skill_dir = tmp_path / "skills" / "analysis"
    skill_dir.mkdir(parents=True)
    tool_file = skill_dir / "summarize.py"
    tool_file.write_text(
        """
def get_tool_definition():
    return {
        "name": "summarize",
        "description": "Summarize text",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}
    }

async def execute(arguments):
    return "summary"
""",
        encoding="utf-8",
    )

    executor.register_skill_bundle(
        skill_name="analysis",
        dir_path=str(skill_dir),
        tool_files=[str(tool_file)],
        allowed_tools=[],
    )
    assert "summarize" not in executor.get_tool_names()

    activated = await executor.activate_skill("analysis")
    assert activated == ["analysis__summarize"]
    assert "analysis__summarize" in executor.get_tool_names()


@pytest.mark.asyncio
async def test_activate_skill_only_registers_allowed_tools(tmp_path):
    executor = ToolExecutor(validate_calls=False)
    skill_dir = tmp_path / "skills" / "ops"
    skill_dir.mkdir(parents=True)

    (skill_dir / "allowed_tool.py").write_text(
        """
def get_tool_definition():
    return {"name": "allowed_tool", "description": "allowed", "parameters": {"type": "object", "properties": {}}}

async def execute(arguments):
    return "ok"
""",
        encoding="utf-8",
    )
    (skill_dir / "blocked_tool.py").write_text(
        """
def get_tool_definition():
    return {"name": "blocked_tool", "description": "blocked", "parameters": {"type": "object", "properties": {}}}

async def execute(arguments):
    return "blocked"
""",
        encoding="utf-8",
    )

    executor.register_skill_bundle(
        skill_name="ops",
        dir_path=str(skill_dir),
        tool_files=[
            str(skill_dir / "allowed_tool.py"),
            str(skill_dir / "blocked_tool.py"),
        ],
        allowed_tools=["allowed_tool"],
    )
    activated = await executor.activate_skill("ops")
    assert activated == ["ops__allowed_tool"]
    assert "ops__allowed_tool" in executor.get_tool_names()
    assert "ops__blocked_tool" not in executor.get_tool_names()
