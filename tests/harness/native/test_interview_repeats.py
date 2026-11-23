"""Repeated records remain ID-scoped through official SDK dispatch/persistence."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ToolReturnPart

from jvagent.action.interview import InterviewAction
from jvagent.action.interview.session import load_session
from tests.harness.native.test_interview import host, turn


def results(result: Any, name: str) -> list[dict[str, Any]]:
    return [
        json.loads(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


@pytest.mark.asyncio
async def test_native_repeated_item_reorder_validation_and_confirmation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "repeat"
    directory.mkdir()
    metadata = {
        "name": "repeat",
        "description": "Item registration",
        "extends": "action:jvagent/interview",
        "interview": {
            "confirm": "manual",
            "fields": [
                {
                    "key": "item_ids",
                    "prompt": "Which items?",
                    "validator": "text",
                    "for_each": {
                        "fields": [
                            {"key": "title", "prompt": "Title?", "validator": "text"},
                            {
                                "key": "quantity",
                                "prompt": "Quantity?",
                                "validator": "number",
                            },
                            {
                                "key": "notes",
                                "prompt": "Notes?",
                                "validator": "text",
                                "required": False,
                            },
                        ]
                    },
                }
            ],
        },
    }
    (directory / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(metadata) + "---\nRegister the user's item records.\n"
    )
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    assert "interview__set_items" in {tool.name for tool in tools}
    first, _ = await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "repeat"}),
            (
                "interview__set_items",
                {"parent_key": "item_ids", "items": [{"id": "A1"}, {"id": "C3"}]},
            ),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "item_ids",
                    "item_id": "C3",
                    "fields": {"quantity": "invalid"},
                },
            ),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "item_ids",
                    "item_id": "C3",
                    "fields": {"title": "Gamma", "quantity": "3"},
                    "skip_fields": ["notes"],
                },
            ),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "item_ids",
                    "item_id": "A1",
                    "fields": {"title": "Alpha", "quantity": 1},
                    "skip_fields": ["notes"],
                },
            ),
            ("interview__review", {}),
        ],
        bases,
    )
    assert results(first, "interview__set_item_fields")[0]["ok"] is False
    state = load_session(visitor.conversation).context["for_each"]["item_ids"]
    assert state["status"] == "complete"
    assert [r["item_id"] for r in state["records"]] == ["A1", "C3"]
    second, _ = await turn(
        directory,
        visitor,
        tools,
        [
            (
                "interview__set_items",
                {
                    "parent_key": "item_ids",
                    "items": [{"id": "A1"}, {"id": "B2"}, {"id": "C3"}],
                },
            ),
            ("interview__review", {}),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "item_ids",
                    "item_id": "B2",
                    "fields": {"title": "Beta", "quantity": 2},
                    "skip_fields": ["notes"],
                },
            ),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "item_ids",
                    "item_id": "C3",
                    "fields": {"quantity": "4"},
                },
            ),
            ("interview__complete", {}),
            ("interview__review", {}),
        ],
        bases,
    )
    assert results(second, "interview__review")[0]["ok"] is False
    assert results(second, "interview__complete")[0]["ok"] is False
    session = load_session(visitor.conversation)
    records = session.context["for_each"]["item_ids"]["records"]
    assert [(r["item_id"], r["fields"]["quantity"]) for r in records] == [
        ("A1", "1"),
        ("B2", "2"),
        ("C3", "4"),
    ]
    assert visitor.tasks.list()[0].status == "active"
    await turn(directory, visitor, tools, [("interview__complete", {})], bases)
    assert load_session(visitor.conversation) is None
    assert visitor.tasks.list()[0].status == "completed"


@pytest.mark.asyncio
async def test_native_repeated_writes_cannot_bypass_validation_or_item_scope(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "scope"
    directory.mkdir()
    metadata = {
        "name": "scope",
        "description": "Scoped items",
        "extends": "action:jvagent/interview",
        "interview": {
            "fields": [
                {
                    "key": "items",
                    "prompt": "Items?",
                    "validator": "text",
                    "for_each": {
                        "fields": [
                            {
                                "key": "quantity",
                                "prompt": "Quantity?",
                                "validator": "number",
                            },
                            {
                                "key": "notes",
                                "prompt": "Notes?",
                                "required": False,
                                "validator": "text",
                            },
                        ]
                    },
                }
            ]
        },
    }
    (directory / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(metadata) + "---\nCollect item answers.\n"
    )
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    result, _ = await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "scope"}),
            (
                "interview__set_items",
                {"parent_key": "items", "items": [{"id": "A1"}, {"id": "B2"}]},
            ),
            (
                "interview__set_fields",
                {
                    "fields": {"items": "X1"},
                    "for_each_staged": {"2": {"quantity": "invalid"}},
                },
            ),
            ("interview__set_fields", {"fields": {"quantity": "9"}}),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "items",
                    "item_id": "A1",
                    "fields": {"quantity": {"value": 9}},
                },
            ),
            (
                "interview__set_item_fields",
                {"parent_key": "items", "item_id": "A1", "fields": {"quantity": 2.5}},
            ),
            (
                "interview__set_item_fields",
                {"parent_key": "items", "item_id": "X1", "fields": {"quantity": "9"}},
            ),
            (
                "interview__set_item_fields",
                {"parent_key": "items", "item_id": "A1", "skip_fields": ["quantity"]},
            ),
            (
                "interview__set_item_fields",
                {
                    "parent_key": "items",
                    "item_id": "B2",
                    "fields": {"quantity": "invalid"},
                    "skip_fields": ["notes"],
                },
            ),
            (
                "interview__set_items",
                {"parent_key": "items", "items": [{"id": "A1"}, {"id": "A1"}]},
            ),
        ],
        await action.get_native_skill_bases(),
    )
    assert all(not item["ok"] for item in results(result, "interview__set_fields"))
    item_returns = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == "interview__set_item_fields"
    ]
    # Nested objects fail at the SDK schema boundary; domain-invalid scalar
    # answers still reach the interview validator and return its JSON result.
    assert len(item_returns) == 5
    assert item_returns[0] == "Tool arguments do not match the declared schema"
    assert all(not json.loads(value)["ok"] for value in item_returns[1:])
    assert results(result, "interview__set_items")[-1]["ok"] is False
    session = load_session(visitor.conversation)
    assert session.fields == {"items": "A1, B2"}
    assert not session.context.get("_for_each_staged")
    state = session.context["for_each"]["items"]
    assert [item["id"] for item in state["items"]] == ["A1", "B2"]
    assert state["records"] == []
