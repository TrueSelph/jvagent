"""Branch pivot and review binding through native SDK tool execution."""

import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ToolReturnPart

from jvagent.action.interview import InterviewAction
from jvagent.action.interview.session import load_session
from tests.harness.native.test_interview import host, turn


@pytest.mark.asyncio
async def test_native_branch_pivot_preserves_shared_fields_and_rebinds_review(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "branch"
    directory.mkdir()
    metadata = {
        "name": "branch",
        "description": "Branch intake",
        "extends": "action:jvagent/interview",
        "interview": {
            "confirm": "manual",
            "fields": [
                {
                    "key": "tier",
                    "prompt": "Premium or standard?",
                    "validator": "text",
                    "branches": [
                        {
                            "when": {"op": "equals", "value": "premium"},
                            "goto": "premium_note",
                        }
                    ],
                    "else": "standard_note",
                },
                {
                    "key": "premium_note",
                    "prompt": "Premium detail?",
                    "validator": "text",
                    "else": "email",
                },
                {
                    "key": "standard_note",
                    "prompt": "Standard detail?",
                    "validator": "text",
                    "else": "email",
                },
                {"key": "email", "prompt": "Email?", "validator": "email"},
            ],
        },
    }
    (directory / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(metadata)
        + "---\nCollect and review the reachable fields.\n"
    )
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    assert "interview__start" in {tool.name for tool in tools}
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "branch"}),
            (
                "interview__set_fields",
                {
                    "fields": {
                        "tier": "premium",
                        "premium_note": "Premium detail",
                        "email": "member@example.com",
                    }
                },
            ),
            ("interview__review", {}),
        ],
        bases,
    )
    assert load_session(visitor.conversation).fields["premium_note"] == "Premium detail"
    result, _ = await turn(
        directory,
        visitor,
        tools,
        [
            (
                "interview__set_fields",
                {"fields": {"tier": "standard", "standard_note": "Standard detail"}},
            ),
            ("interview__complete", {}),
            ("interview__review", {}),
        ],
        bases,
    )
    session = load_session(visitor.conversation)
    assert session.fields == {
        "tier": "standard",
        "standard_note": "Standard detail",
        "email": "member@example.com",
    }
    assert "premium_note" in session.context["pruned_fields"]
    from jvspatial.core.context import get_default_context

    rows = await get_default_context().database.find("node", {"entity": "Conversation"})
    persisted = next(
        row["context"] for row in rows if row["id"] == visitor.conversation.id
    )
    assert persisted["context"]["interview"]["fields"] == session.fields
    assert (
        "premium_note" in persisted["context"]["interview"]["context"]["pruned_fields"]
    )
    assert persisted["tasks"][0]["status"] == "active"
    completion = [
        json.loads(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "interview__complete"
    ]
    assert completion[0]["ok"] is False
    assert completion[0]["next_tool"] == "interview__review"
    assert visitor.tasks.list()[0].status == "active"
    await turn(directory, visitor, tools, [("interview__complete", {})], bases)
    assert load_session(visitor.conversation) is None
    assert visitor.tasks.list()[0].status == "completed"
