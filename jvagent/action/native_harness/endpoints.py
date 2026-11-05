"""Native harness uses the standard authenticated interaction endpoint."""

from decimal import Decimal
from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import (
    AuthenticationError,
    ResourceNotFoundError,
    ValidationError,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError


class UsageReserveRequest(BaseModel):
    """An admin-approved additive estimate, never a provider invoice."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, str_strip_whitespace=True
    )
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    usd: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    evidence: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


async def _scope(action_id: str, interaction_id: str) -> tuple[Any, Any, Any]:
    from jvagent.action.native_harness.native_harness_action import NativeHarnessAction
    from jvagent.core.execution_owner import (
        require_native_local_ownership,
        uses_native_harness,
    )
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

    require_native_local_ownership()
    action = await NativeHarnessAction.get(action_id)
    rows = await Interaction.find({"id": interaction_id})
    if (
        not isinstance(action, NativeHarnessAction)
        or not action.enabled
        or len(rows) != 1
    ):
        raise ResourceNotFoundError("Native recovery scope not found")
    interaction = rows[0]
    conversations = await Conversation.find({"id": interaction.conversation_id})
    if len(conversations) != 1:
        raise ResourceNotFoundError("Native recovery scope not found")
    conversation = conversations[0]
    agent = await conversation.get_agent()
    if (
        agent is None
        or agent.id != action.agent_id
        or not await uses_native_harness(agent)
        or conversation.user_id != interaction.user_id
        or conversation.session_id != interaction.session_id
    ):
        raise ResourceNotFoundError("Native recovery scope not found")
    return action, interaction, conversation


@endpoint(
    "/actions/{action_id}/native-recovery/{interaction_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Native recovery"],
)
async def inspect_native_recovery(
    action_id: str, interaction_id: str
) -> dict[str, Any]:
    """Inspect facts without authorizing replay or returning model/tool content."""
    from jvagent.harness.reconciliation import recovery_digest
    from jvagent.harness.recovery import NativeHistoryError, resolve_history_predecessor
    from jvagent.harness.step_store import InteractionStepStore

    _, interaction, conversation = await _scope(action_id, interaction_id)
    store = InteractionStepStore(interaction)
    records = await store.list_runs()
    try:
        original, notices = await resolve_history_predecessor(
            interaction,
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            session_id=conversation.session_id,
        )
        target = original.id if original is not None else None
        notice_ids = [item.id for item in notices]
        resolution_error = None
    except NativeHistoryError as error:
        target, notice_ids, resolution_error = None, [], str(error)
    return {
        "interaction_id": interaction.id,
        "status": interaction.native_run.get("status"),
        "run_id": interaction.native_run.get("run_id"),
        "digest": recovery_digest(interaction),
        "unknown_cost_call_count": interaction.usage.get("unknown_cost_call_count", 0),
        "recorded_runs": len(records),
        "unresolved_effects": sum(
            [
                len(await store.list_unresolved_tool_effects(run_id=r.run_id))
                for r in records
            ]
        ),
        "usage_reserve": interaction.native_run.get("usage_reserve"),
        "recovery_interaction_id": target,
        "blocked_interaction_ids": notice_ids,
        "resolution_error": resolution_error,
    }


@endpoint(
    "/actions/{action_id}/native-recovery/{interaction_id}/usage-reserve",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Native recovery"],
)
async def apply_usage_reserve(
    request: Request,
    action_id: str,
    interaction_id: str,
    reserve: UsageReserveRequest,
) -> dict[str, Any]:
    """Charge the shared budget before acknowledging a bounded usage reserve."""
    # jvspatial's public wrapper materializes nested models as dictionaries.
    # Revalidate here so direct/missing-body invocation has the same boundary.
    try:
        reserve = UsageReserveRequest.model_validate(reserve)
    except PydanticValidationError:
        raise ValidationError(
            "A complete valid usage reserve request is required"
        ) from None
    from pydantic_ai.models import infer_model

    from jvagent.harness.reconciliation import reserve_unknown_usage
    from jvagent.memory.distributed_conversation_lock import conversation_mutation_lock

    principal = getattr(request.state, "user", None)
    actor = (
        principal.get("id")
        if isinstance(principal, dict)
        else getattr(principal, "id", None)
    )
    if not actor:
        raise AuthenticationError("An authenticated operator identity is required")
    _, _, conversation = await _scope(action_id, interaction_id)
    async with conversation_mutation_lock(conversation.id):
        action, interaction, conversation = await _scope(action_id, interaction_id)
        try:
            receipt = await reserve_unknown_usage(
                interaction,
                conversation,
                **reserve.model_dump(),
                actor=str(actor),
                model=infer_model(action.model),
                run_limit=action.max_run_cost_usd,
                conversation_limit=action.max_conversation_cost_usd,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error
    return {"receipt": receipt, "usage": interaction.usage}
