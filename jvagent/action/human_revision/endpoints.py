"""Human Approval Action — Admin Endpoints.

Provides REST endpoints for:
  - Manually triggering an approval request (useful for testing).
  - Listing pending/waiting approval requests for an agent.
  - Admin-overriding a pending request (force approve / revise / reject).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.core.agent import Agent

from .human_revision_action import RevisionOutcome, RevisionRequest, HumanRevisionAction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_revision_action(agent_id: str) -> HumanRevisionAction:
    """Resolve the HumanRevisionAction for a given agent, raising 404 if absent."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    action = await agent.get_action_by_type("HumanRevisionAction")
    if not action:
        raise ResourceNotFoundError(
            message="HumanRevisionAction not found or not enabled for this agent",
            details={"agent_id": agent_id},
        )
    return action


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@endpoint(
    "/human-revision/{agent_id}/request",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    operation_id="trigger_revision_request",
    tags=["Human Revision"],
    response=success_response(
        data={
            "context_key": ResponseField(field_type=str, example="my-content-123"),
            "status": ResponseField(field_type=str, example="pending"),
            "message": ResponseField(field_type=str, example="Review request sent to reviewer."),
        }
    ),
)
async def trigger_revision_request(
    agent_id: str,
    payload_text: str,
    context_key: str,
    callback_action: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Manually trigger a human revision request for an agent.

    Useful for testing the review flow without going through the full broadcast pipeline.

    Args:
        agent_id:        The ID of the agent whose HumanRevisionAction to use.
        payload_text:    The content to send to the reviewer for review.
        context_key:     A unique key identifying this review (e.g. a script ID).
        callback_action: Class name of the action to call back (optional for manual tests).
        metadata:        Arbitrary metadata dict to echo back in the callback.

    Returns:
        Status and context_key of the created revision request.
    """
    action = await _get_revision_action(agent_id)

    request = await action.request_approval(
        payload_text=payload_text,
        context_key=context_key,
        callback_action=callback_action,
        metadata=metadata or {},
        agent_id=agent_id,
    )

    msg = (
        f"Review request queued (waiting — another review is active)."
        if request.status == "waiting"
        else f"Review request sent to {action.reviewer_phone}."
    )

    return {
        "context_key": request.context_key,
        "status": request.status,
        "message": msg,
    }


@endpoint(
    "/human-revision/{agent_id}/requests",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    operation_id="list_revision_requests",
    tags=["Human Revision"],
    response=success_response(
        data={
            "requests": ResponseField(field_type=list, example=[]),
        }
    ),
)
async def list_revision_requests(
    agent_id: str,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """List revision requests for an agent, optionally filtered by status.

    Args:
        agent_id: The ID of the agent.
        status:   Optional filter — one of pending | waiting | approved | revision | rejected.

    Returns:
        List of revision request summaries.
    """
    await _get_revision_action(agent_id)  # validates agent + action exist

    query: Dict[str, Any] = {"context.metadata.agent_id": agent_id}
    if status:
        query["context.status"] = status

    try:
        requests = await RevisionRequest.find(query)
    except Exception as e:
        logger.error(f"Error listing revision requests for agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch revision requests.")

    return {
        "requests": [
            {
                "context_key": r.context_key,
                "status": r.status,
                "callback_action": r.callback_action,
                "reviewer_phone": r.reviewer_phone,
                "created_at": r.created_at,
                "reviewer_reply": r.reviewer_reply,
                "revision_instructions": r.revision_instructions,
            }
            for r in (requests or [])
        ]
    }


@endpoint(
    "/human-revision/{agent_id}/override",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    operation_id="override_revision_request",
    tags=["Human Revision"],
    response=success_response(
        data={
            "context_key": ResponseField(field_type=str, example="my-content-123"),
            "outcome": ResponseField(field_type=str, example="approved"),
            "message": ResponseField(field_type=str, example="Override applied successfully."),
        }
    ),
)
async def override_revision_request(
    agent_id: str,
    context_key: str,
    outcome: str,
    revision_instructions: Optional[str] = None,
) -> Dict[str, Any]:
    """Force-resolve a pending revision request without waiting for the reviewer to reply.

    This is an admin escape hatch — useful when the reviewer is unavailable or
    when testing individual outcome paths.

    Args:
        agent_id:              The ID of the agent.
        context_key:           The unique key of the revision request to override.
        outcome:               One of ``approved`` | ``revision`` | ``rejected``.
        revision_instructions: Optional instructions to attach when outcome is ``revision``.

    Returns:
        The outcome that was applied and the context_key.
    """
    action = await _get_revision_action(agent_id)

    # Validate outcome
    try:
        parsed_outcome = RevisionOutcome(outcome.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome '{outcome}'. Must be one of: approved, revision, rejected.",
        )

    if parsed_outcome == RevisionOutcome.UNKNOWN:
        raise HTTPException(status_code=400, detail="Cannot override with outcome 'unknown'.")

    # Find the request
    try:
        request = await RevisionRequest.find_one(
            {
                "context.context_key": context_key,
                "context.metadata.agent_id": agent_id,
                "context.status": "pending",
            }
        )
    except Exception as e:
        logger.error(f"Error finding revision request '{context_key}': {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch revision request.")

    if not request:
        raise ResourceNotFoundError(
            message=f"No pending revision request found with context_key '{context_key}' for agent '{agent_id}'.",
            details={"context_key": context_key, "agent_id": agent_id},
        )

    # Apply override
    request.status = parsed_outcome.value
    request.reviewer_reply = f"[ADMIN OVERRIDE: {parsed_outcome.value}]"
    request.revision_instructions = revision_instructions or ""
    await request.save()

    logger.info(
        f"HumanRevision admin override: '{context_key}' → '{parsed_outcome.value}'"
        + (f" (instructions: {revision_instructions})" if revision_instructions else "")
    )

    # Invoke callback
    await action._invoke_callback(request, parsed_outcome, revision_instructions)

    # Promote next waiting
    await action._promote_next_waiting(agent_id)

    return {
        "context_key": context_key,
        "outcome": parsed_outcome.value,
        "message": "Override applied successfully.",
    }
