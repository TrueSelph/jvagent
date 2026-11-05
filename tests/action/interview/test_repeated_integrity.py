"""Stable repeated-item identities and review binding are domain invariants."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.engine import handle_complete, handle_review
from jvagent.action.interview.for_each import (
    auto_advance_staged_for_each,
    complete_current_item,
    init_expansion,
)
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import FieldDef, ForEachDef, InterviewSpec


def declaration() -> InterviewSpec:
    return InterviewSpec(
        name="items",
        fields=[
            FieldDef(
                key="items",
                prompt="Items?",
                for_each=ForEachDef(
                    fields=[FieldDef(key="title", prompt="Title?", validator="text")]
                ),
            )
        ],
    )


def test_inserting_item_preserves_record_identity_and_order() -> None:
    spec = declaration()
    parent = spec.fields[0]
    session = InterviewSession(interview_type="items", fields={"items": "A, C"})
    init_expansion(session, "items", parent, ["A", "C"])
    for label in ("Title A", "Title C"):
        session.set_value("title", label)
        complete_current_item(session, spec)
    init_expansion(session, "items", parent, ["A", "B", "C"])
    session.set_value("title", "Title B")
    complete_current_item(session, spec)
    auto_advance_staged_for_each(session, spec)
    state = session.context["for_each"]["items"]
    assert state["status"] == "complete"
    assert [r["item_id"] for r in state["records"]] == ["A", "B", "C"]
    assert [r["fields"]["title"] for r in state["records"]] == [
        "Title A",
        "Title B",
        "Title C",
    ]


@pytest.mark.asyncio
async def test_changed_repeated_record_requires_fresh_review() -> None:
    spec = declaration()
    session = InterviewSession(interview_type="items", fields={"items": "A"})
    init_expansion(session, "items", spec.fields[0], ["A"])
    session.set_value("title", "Old title")
    complete_current_item(session, spec)
    action = SimpleNamespace(
        _get_session_and_contract=AsyncMock(return_value=(session, spec)),
        _load_fn=lambda spec: lambda name: None,
        _save_session=AsyncMock(),
        _clear_interview_session=AsyncMock(),
        _close_task=AsyncMock(),
    )
    await handle_review(
        action, SimpleNamespace(interaction=SimpleNamespace(id="review"))
    )
    session.context["for_each"]["items"]["records"][0]["fields"]["title"] = "New title"
    result = json.loads(
        await handle_complete(
            action, SimpleNamespace(interaction=SimpleNamespace(id="confirm"))
        )
    )
    assert result["ok"] is False and result["next_tool"] == "interview__review"
    action._clear_interview_session.assert_not_awaited()
