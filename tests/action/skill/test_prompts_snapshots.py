"""Snapshot-style tests for prompt constants."""

from jvagent.action.mcp import prompts as mcp_prompts
from jvagent.action.skill import prompts as skill_prompts


def test_skill_prompts_version_and_key_phrases():
    assert skill_prompts.SKILL_PROMPTS_VERSION == 2
    assert "You are an intelligent skills-based agent with access to tools" in (
        skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    )
    assert "maximum number of steps allowed" in skill_prompts.FORCED_TERMINATION_PROMPT


def test_mcp_prompts_version_and_shape():
    assert mcp_prompts.MCP_PROMPTS_VERSION == 1
    assert "Output only valid JSON" in mcp_prompts.TOOL_SELECTION_SYSTEM
    assert '"server_name"' in mcp_prompts.TOOL_SELECTION_USER_TEMPLATE
    assert '"tool_name"' in mcp_prompts.TOOL_SELECTION_USER_TEMPLATE
