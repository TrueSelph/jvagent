"""SDK-owned planning persists only within its server-bound graph conversation."""

import asyncio
import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.planning import PlanItem, PlanStore, TaskStatus

from jvagent.core.agent import Agent
from jvagent.harness.native import build_native_agent
from jvagent.harness.plan_store import ConversationPlanStore
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.manager import Memory
from jvagent.memory.user import User


async def stores() -> (
    tuple[Conversation, ConversationPlanStore, ConversationSpendStore]
):
    conversation = await Conversation.create(
        user_id="caller", session_id="planning-session"
    )
    owner = await Agent.create(
        namespace="test",
        name="planner",
        alias="Planner",
        description="Plan store owner",
    )
    memory = await Memory.create(agent_id=owner.id)
    user = await User.create(memory_id=memory.id, user_id="caller")
    await owner.connect(memory, direction="out")
    await memory.connect(user, direction="out")
    await user.connect(conversation, direction="out")
    spend = ConversationSpendStore(conversation)
    plan = ConversationPlanStore(
        conversation,
        agent_id=owner.id,
        user_id=conversation.user_id,
        session_id=conversation.session_id,
        lock=spend.lock,
    )
    assert isinstance(plan, PlanStore)
    return conversation, plan, spend


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_plan_store_reloads_detached_items_and_keeps_spend(
    tmp_path: Path, monkeypatch: Any, deferred: bool
) -> None:
    from pydantic_ai_harness.spend import SpendEntry

    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", str(deferred).lower())
    conversation, plan, spend = await stores()
    await plan.set_items(
        [
            PlanItem(
                id="one", content="Collect requirements", status=TaskStatus.in_progress
            )
        ]
    )
    detached = await plan.get_items()
    detached[0].content = "Tampered"
    assert (await plan.get_item("one")).content == "Collect requirements"
    await asyncio.gather(
        plan.add_item(PlanItem(id="two", content="Draft")),
        spend.add_many(
            [SpendEntry(key="conversation", usd=Decimal("0.25"), token="receipt")]
        ),
    )
    fresh = (await Conversation.find({"id": conversation.id}))[0]
    restored = ConversationPlanStore(
        fresh,
        agent_id=plan.scope["agent_id"],
        user_id=fresh.user_id,
        session_id=fresh.session_id,
        lock=asyncio.Lock(),
    )
    assert [i.id for i in await restored.get_items()] == ["one", "two"]
    assert fresh.native_spend["conversation"]["spent"]["usd"] == "0.25"
    await restored.update_item("one", status=TaskStatus.completed)
    assert (await restored.get_item("one")).status == TaskStatus.completed
    assert await restored.remove_item("two") and not await restored.remove_item(
        "missing"
    )
    assert (await restored.get_item("missing")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["duplicate", "oversized", "foreign-state", "changed-caller"]
)
async def test_invalid_plan_state_or_scope_cannot_replace_current_plan(
    case: str,
) -> None:
    conversation, plan, _ = await stores()
    await plan.set_items([PlanItem(id="one", content="Original")])
    before = copy.deepcopy(conversation.native_plan)
    with pytest.raises(ValueError):
        if case == "duplicate":
            await plan.add_item(PlanItem(id="one", content="Duplicate"))
        if case == "oversized":
            await plan.set_items([PlanItem(content="x" * 65537)])
        if case == "foreign-state":
            foreign = await Conversation.create(user_id="caller", session_id="foreign")
            object.__setattr__(foreign, "native_plan", before)
            await foreign.save()
            await ConversationPlanStore(
                foreign,
                agent_id=plan.scope["agent_id"],
                user_id=foreign.user_id,
                session_id=foreign.session_id,
                lock=asyncio.Lock(),
            ).get_items()
        if case == "changed-caller":
            conversation.user_id = "foreign"
            await plan.get_items()
    assert conversation.native_plan == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["write", "read-only", "denied", "denied-write", "late-failure", "flush-failure"],
)
async def test_sdk_planning_owns_tools_and_mutation_receipts(
    tmp_path: Path, mode: str, monkeypatch: Any
) -> None:
    conversation, plan, _ = await stores()
    interaction = await Interaction.create(
        conversation_id=conversation.id,
        user_id="caller",
        session_id=conversation.session_id,
        utterance="Plan the work",
    )
    interaction.native_run = {"run_id": "plan-run", "status": "running"}
    store = InteractionStepStore(interaction, agent_id="agent")
    skill = tmp_path / "planning"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: planning\ndescription: Plan work\nallowed-tools: [write_plan, read_plan]\n---\nUse the official planning tools.\n"
    )
    if mode == "flush-failure":
        from unittest.mock import AsyncMock

        from jvagent.harness import plan_store as plan_module

        monkeypatch.setattr(
            plan_module,
            "flush_deferred_entities",
            AsyncMock(side_effect=OSError("Unconfirmed plan persistence")),
        )
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert {t.name for t in info.function_tools}.intersection(
            {"write_plan", "read_plan"}
        ) == ({"read_plan"} if mode == "read-only" else {"write_plan", "read_plan"})
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_plan",
                        {
                            "items": [
                                {
                                    "id": "one",
                                    "content": "Collect requirements",
                                    "status": "in_progress",
                                }
                            ]
                        },
                        tool_call_id="plan-call",
                    )
                ]
            )
        if mode == "late-failure":
            raise RuntimeError("Provider failed after plan write")
        return ModelResponse(parts=[TextPart("done")])

    async def check(name: str) -> bool:
        return not (
            (mode == "denied" and name == "read_plan")
            or (mode == "denied-write" and name == "write_plan")
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=[],
        access_check=check,
        instructions="Plan",
        step_store=store,
        conversation_id=conversation.session_id,
        plan_store=plan,
        allow_mutations=mode != "read-only",
    )
    if mode in {"denied", "denied-write", "late-failure", "flush-failure"}:
        with pytest.raises((PermissionError, RuntimeError, OSError)):
            await agent.run(
                "Plan the work",
                run_id="plan-run",
                conversation_id=conversation.session_id,
            )
    else:
        await agent.run(
            "Plan the work", run_id="plan-run", conversation_id=conversation.session_id
        )
    if mode in {"write", "late-failure"}:
        assert (await plan.get_item("one")).content == "Collect requirements"
        effect = await store.get_tool_effect(
            run_id="plan-run", tool_call_id="plan-call"
        )
        assert (
            effect.effect_summary == "jv:mutation:plan:v1"
            and effect.status == "completed"
        )
    elif mode == "flush-failure":
        effect = await store.get_tool_effect(
            run_id="plan-run", tool_call_id="plan-call"
        )
        assert (
            effect.effect_summary == "jv:mutation:plan:v1" and effect.status == "failed"
        )
    else:
        assert await plan.get_items() == []
    if mode in {"late-failure", "flush-failure"}:
        interaction.native_run["status"] = "failed"
        with pytest.raises(
            NativeHistoryError, match="mutation requires reconciliation"
        ):
            await history_for_new_turn(
                interaction,
                conversation_id=conversation.id,
                user_id="caller",
                session_id=conversation.session_id,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing-id", "unknown-field", "invalid-version"])
async def test_plan_read_never_generates_ids_or_drops_stored_schema(
    change: str,
) -> None:
    conversation, store, _ = await stores()
    await store.set_items([PlanItem(id="stable", content="Keep identity")])
    state = copy.deepcopy(conversation.native_plan)
    if change == "missing-id":
        state["items"][0].pop("id")
    if change == "unknown-field":
        state["items"][0]["future_property"] = "retained"
    if change == "invalid-version":
        state["version"] = True
    object.__setattr__(conversation, "native_plan", state)
    await conversation.save()
    with pytest.raises(ValueError):
        await store.get_items()
    assert conversation.native_plan == state


@pytest.mark.asyncio
async def test_plan_backend_rejects_wrong_caller_or_graph_owner() -> None:
    conversation, plan, spend = await stores()
    with pytest.raises(ValueError, match="caller"):
        ConversationPlanStore(
            conversation,
            agent_id=plan.scope["agent_id"],
            user_id="foreign",
            session_id=conversation.session_id,
            lock=spend.lock,
        )
    foreign = ConversationPlanStore(
        conversation,
        agent_id="foreign-agent",
        user_id=conversation.user_id,
        session_id=conversation.session_id,
        lock=spend.lock,
    )
    with pytest.raises(ValueError, match="another agent"):
        await foreign.get_items()
    assert conversation.native_plan == {}
