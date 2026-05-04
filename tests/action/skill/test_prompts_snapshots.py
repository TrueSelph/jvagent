"""Snapshot-style tests for prompt constants."""

from jvagent.action.mcp import prompts as mcp_prompts
from jvagent.action.skill import prompts as skill_prompts


def test_skill_prompts_version_and_key_phrases():
    assert skill_prompts.SKILL_PROMPTS_VERSION == 13
    assert "You are an intelligent skills-based agent with access to tools" in (
        skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    )
    assert "Ground claims in this thread" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    # claw-code-style section headers adopted in Fix 1
    assert "# Doing tasks" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    assert "# Task planning" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    assert "# Executing actions with care" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    # Faithful-reporting rule (direct adoption from claw-code)
    assert "Report outcomes faithfully" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    assert "`task_tracker`" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    assert (
        "Each distinct user-requested action or deliverable is its own tracked step."
        in (skill_prompts.SKILL_AGENT_SYSTEM_PROMPT)
    )
    assert "To minimize round-trips" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    assert (
        "Tool results from retrieval searches are authoritative context injected by the system."
        in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    )
    assert "# Response presentation" in skill_prompts.SKILL_AGENT_SYSTEM_PROMPT
    # Compaction resume instruction is present and has a detectable sentinel
    assert (
        skill_prompts.COMPACT_DIRECT_RESUME_SENTINEL
        in skill_prompts.COMPACT_DIRECT_RESUME_INSTRUCTION
    )
    assert "Resume directly" in skill_prompts.COMPACT_DIRECT_RESUME_INSTRUCTION
    assert "pending steps" in skill_prompts.PENDING_STEPS_NUDGE_PROMPT
    assert '`action="complete"`' in skill_prompts.PENDING_STEPS_NUDGE_PROMPT
    assert "maximum number of steps allowed" in skill_prompts.FORCED_TERMINATION_PROMPT
    assert "{opener}" in skill_prompts.TOOL_CALL_ANNOUNCE_TEMPLATE
    assert "{intent}" in skill_prompts.TOOL_CALL_ANNOUNCE_TEMPLATE
    assert skill_prompts.TOOL_CALL_ANNOUNCE_TEMPLATE.strip().endswith(".")
    assert "{result_line}" in skill_prompts.TOOL_RESULT_ANNOUNCE_TEMPLATE
    assert "{error_line}" in skill_prompts.ERROR_ANNOUNCE_TEMPLATE


def test_mcp_prompts_version_and_shape():
    assert mcp_prompts.MCP_PROMPTS_VERSION == 1
    assert "Output only valid JSON" in mcp_prompts.TOOL_SELECTION_SYSTEM
    assert '"server_name"' in mcp_prompts.TOOL_SELECTION_USER_TEMPLATE
    assert '"tool_name"' in mcp_prompts.TOOL_SELECTION_USER_TEMPLATE
