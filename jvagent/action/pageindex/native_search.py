"""Accounted native completion for the administrative PageIndex search route."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import uuid4

from jvagent.harness.completion_tool import CompleteText

CHANNEL = "pageindex_admin_search"


async def run_native_search(
    action: Any,
    *,
    agent_id: str,
    actor: str,
    query: str,
    search: Callable[[CompleteText], Awaitable[list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Keep one isolated graph operation and shared budget across tree ranks."""
    from jvspatial import flush_deferred_entities
    from jvspatial.core.context import get_default_context, scoped_default_context_async
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.usage import RunUsage, UsageLimits
    from pydantic_ai_harness import StepPersistence
    from pydantic_ai_harness.spend import SpendLimitExceeded, SpendLimits

    from jvagent.core.execution_owner import (
        native_worker_metadata,
        require_native_local_ownership,
    )
    from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
    from jvagent.harness.contracts import OutputTokenLimitReached
    from jvagent.harness.model_settings import validate_model_settings
    from jvagent.harness.native_model import qualified_model
    from jvagent.harness.run_limits import effective_run_limits
    from jvagent.harness.spend_store import ConversationSpendStore, native_budgets
    from jvagent.harness.step_store import InteractionStepStore
    from jvagent.memory.conversation import Conversation

    require_native_local_ownership()
    limits = effective_run_limits(action, CHANNEL)
    settings = validate_model_settings(action.model_settings)
    budgets = native_budgets(
        limits["max_run_cost_usd"], action.max_conversation_cost_usd
    )
    model = qualified_model(action.model)
    usage = RunUsage()
    usage_limits = UsageLimits(
        request_limit=limits["max_requests"],
        total_tokens_limit=limits["max_tokens"],
        output_tokens_limit=limits["max_output_tokens"],
        cost_limit=Decimal(limits["max_run_cost_usd"]),
    )
    graph = get_default_context()
    run_id = uuid4().hex
    conversation = await Conversation.create(
        session_id=run_id, user_id=actor, channel=CHANNEL, status="closed"
    )
    await flush_deferred_entities(conversation, strict=True)
    await action.connect(conversation, direction="out")
    interaction = await conversation.create_interaction(query, session_id=run_id)
    store = InteractionStepStore(interaction, agent_id=agent_id)
    recorder = NativeSpendRecorder(interaction, lock=store.lock)
    spend = SpendLimits(
        store=ConversationSpendStore(conversation),
        on_unpriced="raise",
        on_spend=recorder.record,
        budgets=budgets,
    )

    class ReceiptContext(AbstractCapability[Any]):
        """Bind the official spend hook to its admitted SDK run."""

        async def wrap_model_request(
            self, ctx: Any, *, request_context: Any, handler: Any
        ) -> Any:
            with recorder.bind(ctx):
                return await handler(request_context)

    class BudgetAdmission(AbstractCapability[Any]):
        """Record a spend refusal after the SDK journals request start."""

        async def before_model_request(self, ctx: Any, request_context: Any) -> Any:
            try:
                return await spend.before_model_request(ctx, request_context)
            except SpendLimitExceeded:
                await recorder.record_preflight_refusal(ctx, store=store)
                raise

    metadata = {
        **store.registration_metadata(),
        **native_worker_metadata(),
        "jv_operation": CHANNEL,
    }
    receipt = {"run_id": run_id, "status": "running", "limits": limits}
    interaction.native_run = receipt
    await interaction.save()
    await flush_deferred_entities(interaction, strict=True)
    first = True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limits["timeout_seconds"]

    async def complete(prompt: str) -> str:
        nonlocal first
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("PageIndex search deadline reached")
        identity = run_id if first else uuid4().hex
        persistence = StepPersistence(
            store=store, metadata=metadata, parent_run_id=None if first else run_id
        )
        first = False
        async with scoped_default_context_async(graph):
            worker = Agent(
                model,
                name="pageindex_admin_search",
                model_settings=settings,
                capabilities=[
                    ReceiptContext(),
                    persistence,
                    BudgetAdmission(),
                    spend,
                ],
            )
            result = await worker.run(
                prompt,
                run_id=identity,
                conversation_id=run_id,
                usage=usage,
                usage_limits=usage_limits,
                timeout=remaining,
            )
        if result.response.finish_reason == "length":
            raise OutputTokenLimitReached("PageIndex ranking output was truncated")
        return result.output

    try:
        results = await search(complete)
    except BaseException as exc:
        interaction.native_run = {
            **receipt,
            "status": (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            ),
            "failure_type": type(exc).__name__,
            "usage_complete": False,
        }
        raise
    else:
        interaction.native_run = {
            **receipt,
            "status": "completed",
            "usage_complete": True,
        }
        interaction.response = json.dumps(results, ensure_ascii=False)
        return results
    finally:
        await recover_native_usage(interaction, run_id=run_id, model=action.model)
        await interaction.close_interaction()
        await interaction.save()
        await flush_deferred_entities(interaction, strict=True)
