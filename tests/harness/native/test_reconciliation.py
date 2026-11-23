"""Operator reserves retain uncertainty and consume official shared budgets."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.spend import SpendLimits

from jvagent.harness.accounting import recover_native_usage
from jvagent.harness.reconciliation import (
    has_usage_reserve,
    recovery_digest,
    reserve_unknown_usage,
)
from jvagent.harness.recovery import history_for_new_turn
from jvagent.harness.spend_store import ConversationSpendStore, native_budgets
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from tests.harness.native.test_recovery import failed_turn


async def pending(tmp_path: Path) -> tuple[Interaction, Conversation]:
    previous = await failed_turn(tmp_path, paid=False)
    conversation = await Conversation.create(user_id="caller", session_id="session")
    previous.conversation_id = conversation.id
    previous.session_id = "session"
    await recover_native_usage(previous, run_id="failed", model="test")
    await previous.save()
    return previous, conversation


async def reserve(
    previous: Interaction, conversation: Conversation, **changes: Any
) -> dict[str, Any]:
    arguments = dict(
        digest=recovery_digest(previous),
        usd=Decimal("0.50"),
        actor="admin",
        evidence="policy: conservative request allowance",
        reason="Interrupted provider billing is not available",
        model=TestModel(),
        run_limit="2",
        conversation_limit="20",
    )
    arguments.update(changes)
    return await reserve_unknown_usage(previous, conversation, **arguments)


@pytest.mark.asyncio
async def test_reserve_unblocks_input_without_erasing_unknowns(tmp_path: Path) -> None:
    previous, conversation = await pending(tmp_path)
    before = dict(previous.native_steps)
    receipt = await reserve(previous, conversation)
    assert receipt["status"] == "applied" and receipt["actor"] == "admin"
    assert has_usage_reserve(previous)
    assert previous.usage["unknown_cost_call_count"] == 1
    assert previous.usage["estimated_cost_usd"] == 0.5
    assert previous.native_steps == before
    await reserve(previous, conversation)
    assert previous.usage["estimated_cost_usd"] == 0.5
    messages, source = await history_for_new_turn(
        previous,
        conversation_id=conversation.id,
        user_id="caller",
        session_id="session",
    )
    assert messages and source["frontier"] == "accepted_input"
    statuses = await SpendLimits(
        store=ConversationSpendStore(conversation), budgets=native_budgets("2", ".25")
    ).status(
        RunContext(
            deps=None,
            model=TestModel(),
            usage=RunUsage(),
            run_id="failed",
            conversation_id="session",
        )
    )
    assert len(statuses) == 2
    assert all(status.spent.usd == Decimal(".5") for status in statuses)
    assert all(status.spent.requests == 0 for status in statuses)
    assert next(s for s in statuses if s.budget.window == "conversation").exhausted
    fresh = (await Interaction.find({"id": previous.id}))[0]
    assert has_usage_reserve(fresh)
    assert fresh.usage["estimated_cost_usd"] == 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["digest", "amount", "scope", "effects"])
async def test_invalid_or_effectful_reserve_fails_closed(
    tmp_path: Path, change: str
) -> None:
    previous, conversation = await pending(tmp_path)
    options: dict[str, Any] = {}
    if change == "digest":
        options["digest"] = "sha256:" + "0" * 64
    if change == "amount":
        options["usd"] = Decimal("NaN")
    if change == "scope":
        conversation.user_id = "other"
    if change == "effects":
        from pydantic_ai_harness.step_persistence import ToolEffectRecord

        from jvagent.harness.step_store import InteractionStepStore

        await InteractionStepStore(previous).record_tool_effect(
            ToolEffectRecord(
                run_id="failed",
                tool_call_id="effect",
                tool_name="external",
                status="started",
            )
        )
    with pytest.raises(ValueError):
        await reserve(previous, conversation, **options)
    assert not conversation.native_spend
    assert not has_usage_reserve(previous)


@pytest.mark.asyncio
async def test_ack_failure_retries_without_charging_twice(
    tmp_path: Path, monkeypatch: Any
) -> None:
    previous, conversation = await pending(tmp_path)
    original = Interaction.save
    failed = False

    async def save(self: Interaction, *args: Any, **kwargs: Any) -> Any:
        nonlocal failed
        if (
            self.id == previous.id
            and self.native_run.get("usage_reserve", {}).get("status") == "applied"
            and not failed
        ):
            failed = True
            raise OSError("acknowledgement unavailable")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Interaction, "save", save)
    with pytest.raises(OSError):
        await reserve(previous, conversation)
    fresh = (await Interaction.find({"id": previous.id}))[0]
    assert not has_usage_reserve(fresh)
    assert fresh.native_run["usage_reserve"]["status"] == "prepared"
    await reserve(fresh, conversation)
    assert has_usage_reserve(fresh)
    assert all(
        Decimal(item["spent"]["usd"]) == Decimal(".5")
        for item in conversation.native_spend.values()
    )
    with pytest.raises(ValueError, match="cannot be replaced"):
        await reserve(fresh, conversation, usd=Decimal(".6"))


@pytest.mark.asyncio
async def test_late_execution_fact_invalidates_approval(tmp_path: Path) -> None:
    from pydantic_ai_harness.step_persistence import StepEvent

    from jvagent.harness.step_store import InteractionStepStore

    previous, conversation = await pending(tmp_path)
    await reserve(previous, conversation)
    await InteractionStepStore(previous).append_event(
        StepEvent(run_id="failed", kind="run_failed", step_index=99)
    )
    assert not has_usage_reserve(previous)
    with pytest.raises(RuntimeError, match="provider usage requires reconciliation"):
        await history_for_new_turn(
            previous,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )


@pytest.mark.asyncio
async def test_wrapped_dictionary_request_uses_authenticated_actor(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import Request

    from jvagent.action.native_harness import endpoints

    previous, conversation = await pending(tmp_path)
    action = SimpleNamespace(
        model=TestModel(), max_run_cost_usd="2", max_conversation_cost_usd="20"
    )
    monkeypatch.setattr(
        endpoints, "_scope", AsyncMock(return_value=(action, previous, conversation))
    )
    request = Request({"type": "http"})
    request.state.user = {"id": "authenticated-operator"}
    payload = {
        "digest": recovery_digest(previous),
        "usd": "0.5",
        "evidence": "test policy",
        "reason": "unknown interrupted billing",
    }
    result = await endpoints.apply_usage_reserve(
        request, "action", previous.id, payload
    )
    assert result["receipt"]["actor"] == "authenticated-operator"
    assert has_usage_reserve(previous)
