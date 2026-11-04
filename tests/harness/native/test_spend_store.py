"""Official batched spend-store serialization and replay boundaries."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai_harness.spend import BatchSpendStore, SpendEntry

from jvagent.harness.spend_store import ConversationSpendStore


@pytest.mark.asyncio
async def test_spend_batch_replay_and_reload() -> None:
    disk = {}
    conversation = SimpleNamespace(native_spend={})

    async def save() -> None:
        disk.update(json.loads(json.dumps(conversation.native_spend)))

    conversation.save = save
    store = ConversationSpendStore(conversation)
    assert isinstance(store, BatchSpendStore)
    entry = SpendEntry(
        key="conversation", usd=Decimal("0.25"), requests=1, tokens=10, token="receipt"
    )
    await store.add_many([entry, entry])
    restored = ConversationSpendStore(SimpleNamespace(native_spend=disk))
    spent = (await restored.get_many(["conversation"]))["conversation"]
    assert spent.usd == Decimal("0.25")
    assert spent.requests == 1
    assert spent.tokens == 10
    assert (await restored.get_many(["missing"]))["missing"].usd == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
async def test_invalid_spend_increment_rejected(value: str) -> None:
    conversation = SimpleNamespace(native_spend={})
    store = ConversationSpendStore(conversation)
    with pytest.raises(ValueError):
        await store.add_many([SpendEntry(key="conversation", usd=Decimal(value))])
    assert not conversation.native_spend


@pytest.mark.asyncio
async def test_official_budget_blocks_later_run_after_graph_reload() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai_harness.spend import Budget, SpendLimitExceeded, SpendLimits

    conversation = SimpleNamespace(native_spend={})

    async def save() -> None:
        pass

    conversation.save = save
    budget = Budget(usd=Decimal("0.20"), window="conversation")
    first = Agent(
        TestModel(call_tools=[]),
        capabilities=[
            SpendLimits(
                store=ConversationSpendStore(conversation),
                budgets=[budget],
                price=lambda response: Decimal("0.25"),
                on_unpriced="raise",
            )
        ],
    )
    await first.run("Hello", run_id="first", conversation_id="session")
    restored = SimpleNamespace(
        native_spend=json.loads(json.dumps(conversation.native_spend)), save=save
    )
    second = Agent(
        TestModel(call_tools=[]),
        capabilities=[
            SpendLimits(
                store=ConversationSpendStore(restored),
                budgets=[budget],
                price=lambda response: Decimal("0.25"),
                on_unpriced="raise",
            )
        ],
    )
    with pytest.raises(SpendLimitExceeded):
        await second.run("Again", run_id="second", conversation_id="session")
