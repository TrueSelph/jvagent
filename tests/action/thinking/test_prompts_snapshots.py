"""Snapshot-style tests for prompt constants."""

from jvagent.action.mcp import prompts as mcp_prompts
from jvagent.action.thinking import prompts as thinking_prompts


def test_thinking_prompts_version_and_key_phrases():
    assert thinking_prompts.THINKING_PROMPTS_VERSION == 1
    assert "You are an intelligent agent with access to tools" in (
        thinking_prompts.THINKING_AGENT_SYSTEM_PROMPT
    )
    assert (
        "maximum number of steps allowed" in thinking_prompts.FORCED_TERMINATION_PROMPT
    )


def test_mcp_prompts_version_and_shape():
    assert mcp_prompts.MCP_PROMPTS_VERSION == 1
    assert "Output only valid JSON" in mcp_prompts.TOOL_SELECTION_SYSTEM
    assert (
        '{"tool_name": "...", "arguments": {...}}'
        in mcp_prompts.TOOL_SELECTION_USER_TEMPLATE
    )
