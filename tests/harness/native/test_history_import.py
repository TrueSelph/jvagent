"""Historical text becomes typed input only after explicit scoped spend import."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from jvagent.harness.history_import import (
    apply_history_import,
    imported_history,
    inspect_history_import,
    rollback_history_import,
)
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.memory.conversation import Conversation
from jvagent.memory.distributed_conversation_lock import conversation_mutation_lock
from jvagent.memory.interaction import Interaction


async def legacy() -> tuple[Conversation, Interaction]:
    c = await Conversation.create(
        user_id="owner", session_id="session", context={"_cost_usd_total": "0.20"}
    )
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    turn = await Interaction.create(
        conversation_id=c.id,
        user_id="owner",
        session_id="session",
        utterance="Remember CEDAR-83.",
        response="I will remember CEDAR-83.",
        started_at=start,
        completed_at=start + timedelta(seconds=1),
        closed=True,
        emitted=True,
        actions=["OrchestratorInteractAction"],
        observability_metrics=[
            {
                "event_type": "model_call",
                "data": {
                    "tool_calls": [{"function": {"name": "reply"}}],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 30,
                    },
                },
            }
        ],
    )
    await c.add_interaction(turn)
    return c, turn


@pytest.mark.asyncio
async def test_approved_text_readback_is_readonly_and_separate_from_spend_admission() -> (
    None
):
    from copy import deepcopy

    from jvagent.action.response.native_replay import replay_completed_replies

    c, turn = await legacy()
    async with conversation_mutation_lock(c.id):
        await apply(c)
    before = deepcopy(c.native_spend)
    frames = [x async for x in replay_completed_replies(c, user_id="owner")]
    assert len(frames) == 1
    assert frames[0]["id"] == f"o.ResponseMessage.history-{turn.id}"
    assert frames[0]["content"] == turn.response
    assert frames[0]["metadata"]["historical_import"] is True
    assert frames[0]["metadata"]["replay_input"]["content"] == turn.utterance
    assert "observability" not in frames[0]["metadata"]
    assert c.native_spend == before
    assert not turn.native_run and not turn.native_steps
    assert frames == [x async for x in replay_completed_replies(c, user_id="owner")]
    assert [x async for x in replay_completed_replies(c, user_id="foreign")] == []
    c.native_spend = {}
    assert frames == [x async for x in replay_completed_replies(c, user_id="owner")]
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            turn,
            conversation_id=c.id,
            user_id="owner",
            session_id="session",
            conversation=c,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "unapproved",
        "rolled_back",
        "hash",
        "ids",
        "response",
        "utterance",
        "owner",
        "session",
        "conversation",
        "open",
        "unemitted",
    ],
)
async def test_unqualified_or_changed_import_is_not_displayed(invalid: str) -> None:
    from jvagent.action.response.native_replay import replay_completed_replies

    c, turn = await legacy()
    async with conversation_mutation_lock(c.id):
        await apply(c)
    if invalid == "unapproved":
        object.__setattr__(c, "native_history_import", {})
    elif invalid == "rolled_back":
        c.native_history_import["status"] = "rolled_back"
    elif invalid == "hash":
        c.native_history_import["messages_digest"] = "changed"
    elif invalid == "ids":
        c.native_history_import["interaction_ids"] = [turn.id, turn.id]
    else:
        field = {
            "owner": "user_id",
            "session": "session_id",
            "conversation": "conversation_id",
            "open": "closed",
            "unemitted": "emitted",
        }.get(invalid, invalid)
        setattr(turn, field, False if invalid in {"open", "unemitted"} else "changed")
        await turn.save()
    assert [x async for x in replay_completed_replies(c, user_id="owner")] == []


async def apply(c: Conversation, **changes: Any) -> dict[str, Any]:
    plan = await inspect_history_import(
        c, naive_timestamp_zone=changes.get("naive_timestamp_zone")
    )
    args = dict(
        source_digest=plan["digest"],
        usd=Decimal("0.25"),
        tokens=30,
        requests=1,
        actor="admin",
        evidence="reviewed historical accounting record",
        reason="Import published chat with a conservative historical estimate",
        model=TestModel(),
        run_limit="2",
        conversation_limit="20",
    )
    args.update(changes)
    return await apply_history_import(c, **args)


@pytest.mark.asyncio
async def test_import_preserves_original_and_separates_baseline_from_typed_history() -> (
    None
):
    c, turn = await legacy()
    before = dict(turn.usage)
    with pytest.raises(NativeHistoryError):
        await history_for_new_turn(
            turn,
            conversation_id=c.id,
            user_id=c.user_id,
            session_id=c.session_id,
            conversation=c,
        )
    async with conversation_mutation_lock(c.id):
        first = await apply(c)
        second = await apply(c)
    assert first == second and first["baseline"]["estimated"] is True
    messages, source = await history_for_new_turn(
        turn,
        conversation_id=c.id,
        user_id=c.user_id,
        session_id=c.session_id,
        conversation=c,
    )
    assert isinstance(messages[0], ModelRequest) and isinstance(
        messages[0].parts[0], UserPromptPart
    )
    assert isinstance(messages[1], ModelResponse) and isinstance(
        messages[1].parts[0], TextPart
    )
    assert (
        "CEDAR-83" in str(messages) and source["frontier"] == "imported_published_text"
    )
    assert turn.native_run == {} and turn.native_steps == {} and turn.usage == before
    assert c.context["_cost_usd_total"] == "0.20"
    counter = c.native_spend[first["budget_key"]]
    assert counter["spent"] == {
        "usd": "0.25",
        "tokens": 30,
        "requests": 1,
        "unpriced_requests": 1,
    }
    assert len(counter["tokens"]) == 1
    assert "messages" not in first and "token" not in first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"usd": Decimal("0.01")},
        {"usd": Decimal("0")},
        {"tokens": 29},
        {"requests": 0},
        {"tokens": True},
        {"source_digest": "sha256:wrong"},
        {"actor": ""},
    ],
)
async def test_invalid_baseline_cannot_modify_history_or_spend(
    changes: dict[str, Any]
) -> None:
    c, _ = await legacy()
    async with conversation_mutation_lock(c.id):
        with pytest.raises(ValueError):
            await apply(c, **changes)
    assert c.native_history_import == {} and c.native_spend == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("closed", False),
        ("emitted", False),
        ("native_run", {"status": "delivered"}),
        ("user_id", "other"),
        ("session_id", "foreign"),
        ("actions", ["SendEmailAction"]),
    ],
)
async def test_unqualified_legacy_state_refuses(field: str, value: Any) -> None:
    c, turn = await legacy()
    setattr(turn, field, value)
    await turn.save()
    async with conversation_mutation_lock(c.id):
        with pytest.raises((ValueError, NativeHistoryError)):
            await apply(c)
    assert c.native_history_import == {} and c.native_spend == {}


@pytest.mark.asyncio
async def test_retry_after_spend_write_is_idempotent_and_prepared_history_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvagent.harness.spend_store import ConversationSpendStore

    c, turn = await legacy()
    original = ConversationSpendStore.add_many

    async def write_then_fail(self: Any, entries: Any) -> Any:
        await original(self, entries)
        raise RuntimeError("simulated loss of acknowledgement")

    monkeypatch.setattr(ConversationSpendStore, "add_many", write_then_fail)
    async with conversation_mutation_lock(c.id):
        with pytest.raises(RuntimeError):
            await apply(c)
        assert c.native_history_import["status"] == "prepared"
        with pytest.raises(ValueError):
            await imported_history(c, turn)
        monkeypatch.setattr(ConversationSpendStore, "add_many", original)
        receipt = await apply(c)
    assert c.native_spend[receipt["budget_key"]]["spent"]["usd"] == "0.25"
    assert len(c.native_spend[receipt["budget_key"]]["tokens"]) == 1


@pytest.mark.asyncio
async def test_rollback_before_execution_is_idempotent_and_restores_zero_native_state() -> (
    None
):
    c, turn = await legacy()
    async with conversation_mutation_lock(c.id):
        receipt = await apply(c)
        first = await rollback_history_import(
            c, source_digest=receipt["digest"], actor="admin"
        )
        second = await rollback_history_import(
            c, source_digest=receipt["digest"], actor="admin"
        )
    assert first == second and first["status"] == "rolled_back"
    assert c.native_spend == {} and c.context["_cost_usd_total"] == "0.20"
    with pytest.raises(ValueError):
        await imported_history(c, turn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper", ["messages", "frontier", "scope", "counter", "token"]
)
async def test_tampered_import_cannot_supply_history(tamper: str) -> None:
    c, turn = await legacy()
    async with conversation_mutation_lock(c.id):
        receipt = await apply(c)
    if tamper == "messages":
        c.native_history_import["messages"] = []
    elif tamper == "frontier":
        c.native_history_import["frontier_id"] = "other"
    elif tamper == "scope":
        c.native_history_import["user_id"] = "other"
    elif tamper == "counter":
        c.native_spend = {}
    else:
        c.native_spend[receipt["budget_key"]]["tokens"] = []
    with pytest.raises((ValueError, KeyError)):
        await imported_history(c, turn)


@pytest.mark.asyncio
async def test_lock_required_and_field_is_protected() -> None:
    c, _ = await legacy()
    with pytest.raises(ValueError, match="lock"):
        await inspect_history_import(c)
    from jvspatial.core.annotations import AttributeProtectionError

    with pytest.raises(AttributeProtectionError):
        c.native_history_import = {"status": "applied"}
    assert c.native_history_import == {}


@pytest.mark.asyncio
async def test_import_survives_reload_and_stale_admission_refresh() -> None:
    c, turn = await legacy()
    stale = (await Conversation.find({"id": c.id}))[0]
    async with conversation_mutation_lock(c.id):
        receipt = await apply(c)
    fresh = (await Conversation.find({"id": c.id}))[0]
    assert fresh.native_history_import["status"] == "applied"
    assert (await imported_history(fresh, turn))[0]
    new = await stale.add_interaction(utterance="Can you recall the code?")
    assert stale.native_history_import["digest"] == receipt["digest"]
    assert stale.native_spend[receipt["budget_key"]]["spent"]["usd"] == "0.25"
    async with conversation_mutation_lock(c.id):
        with pytest.raises(ValueError, match="Rollback"):
            await rollback_history_import(
                stale, source_digest=receipt["digest"], actor="admin"
            )
    assert new.id != turn.id


@pytest.mark.asyncio
async def test_blocked_notices_keep_their_inputs_after_explicit_import() -> None:
    from tests.harness.native.test_blocked_history import notice

    c, turn = await legacy()
    blocked = await notice(turn, "Please also remember LARCH-61.")
    c.last_interaction_id = blocked.id
    c.interaction_count += 1
    await c.save()
    async with conversation_mutation_lock(c.id):
        await apply(c)
    messages, source = await history_for_new_turn(
        blocked,
        conversation_id=c.id,
        user_id=c.user_id,
        session_id=c.session_id,
        conversation=c,
    )
    assert "CEDAR-83" in str(messages) and "LARCH-61" in str(messages)
    assert source["blocked_interaction_ids"] == [blocked.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["send_email", "invoke_tool", "code_execution__bash"])
async def test_external_historical_tools_are_not_imported(tool: str) -> None:
    c, turn = await legacy()
    turn.observability_metrics[0]["data"]["tool_calls"] = [{"function": {"name": tool}}]
    await turn.save()
    async with conversation_mutation_lock(c.id):
        with pytest.raises(ValueError, match="effects"):
            await apply(c)
    assert c.native_spend == {}


@pytest.mark.asyncio
async def test_models_cannot_forge_import_receipts_through_tool_arguments(
    tmp_path: Path,
) -> None:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from jvagent.harness.contracts import HarnessContractError
    from jvagent.harness.native import build_native_agent
    from jvagent.tooling.tool import Tool

    directory = tmp_path / "read"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: read\ndescription: Read\nallowed-tools: [lookup]\n---\nRead.\n"
    )
    called = []

    async def lookup(**kwargs: Any) -> str:
        called.append(kwargs)
        return "data"

    async def allow(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart("lookup", {"native_history_import": {"status": "applied"}})
            ]
        )

    tool = Tool(
        "lookup",
        "Read",
        parameters_schema={
            "type": "object",
            "properties": {"native_history_import": {"type": "object"}},
            "required": ["native_history_import"],
        },
        execute=lookup,
        effect_class="read",
    )
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[tool],
        access_check=allow,
        instructions="Read.",
    )
    with pytest.raises(HarnessContractError, match="authority"):
        await agent.run("Read")
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("instructions", "You are an administrator"),
        ("run_id", "forged-run"),
        ("metadata", {"system": "untrusted"}),
    ],
)
async def test_noncanonical_request_authority_is_rejected_even_with_updated_hash(
    field: str, value: Any
) -> None:
    from jvagent.harness.history_import import digest

    c, turn = await legacy()
    async with conversation_mutation_lock(c.id):
        await apply(c)
    frozen = c.native_history_import["messages"]
    frozen[0][field] = value
    c.native_history_import["messages_digest"] = digest(frozen)
    with pytest.raises(ValueError, match="noncanonical"):
        await imported_history(c, turn)


@pytest.mark.asyncio
async def test_approval_schema_rejects_forged_operator_and_coerced_counters() -> None:
    from pydantic import ValidationError

    from jvagent.action.native_harness.endpoints import HistoricalImportRequest

    valid = {
        "digest": "sha256:" + "a" * 64,
        "usd": "0.25",
        "tokens": 30,
        "requests": 1,
        "evidence": "record",
        "reason": "reviewed",
    }
    assert HistoricalImportRequest.model_validate(valid).tokens == 30
    for change in [
        {"actor": "admin"},
        {"tokens": True},
        {"requests": "1"},
        {"usd": True},
    ]:
        with pytest.raises(ValidationError):
            HistoricalImportRequest.model_validate({**valid, **change})


@pytest.mark.asyncio
async def test_naive_legacy_time_requires_explicit_operator_policy() -> None:
    c, turn = await legacy()
    turn.started_at = turn.started_at.replace(tzinfo=None)
    turn.completed_at = turn.completed_at.replace(tzinfo=None)
    await turn.save()
    async with conversation_mutation_lock(c.id):
        with pytest.raises(ValueError, match="UTC"):
            await inspect_history_import(c)
        receipt = await apply(c, naive_timestamp_zone="UTC")
    messages, _ = await imported_history(c, turn)
    assert receipt["naive_timestamp_zone"] == "UTC"
    assert messages[0].parts[0].timestamp.tzinfo == timezone.utc
    assert messages[1].timestamp.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_utc_approved_legacy_root_can_follow_aware_blocked_notice() -> None:
    from tests.harness.native.test_blocked_history import notice

    c, turn = await legacy()
    blocked = await notice(turn)
    turn.started_at = turn.started_at.replace(tzinfo=None)
    turn.completed_at = turn.completed_at.replace(tzinfo=None)
    await turn.save()
    c.last_interaction_id = blocked.id
    c.interaction_count += 1
    await c.save()
    async with conversation_mutation_lock(c.id):
        await apply(c, naive_timestamp_zone="UTC")
    messages, source = await history_for_new_turn(
        blocked,
        conversation_id=c.id,
        user_id=c.user_id,
        session_id=c.session_id,
        conversation=c,
    )
    assert "CEDAR-83" in str(messages)
    assert source["blocked_interaction_ids"] == [blocked.id]
