import logging
from typing import Dict, Any, Optional
from fastapi import Query, Body, Request
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.task_dispatcher.task_dispatcher import TaskDispatcher

logger = logging.getLogger(__name__)

@endpoint(
    "/proactive/tick/{agent_id}",
    methods=["GET"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["Tasks"],
    response=success_response(
        data={
            "triggered_count": ResponseField(
                field_type=int,
                description="Total number of tasks that were triggered this tick",
                example=1
            ),
            "dispatched_count": ResponseField(
                field_type=int,
                description="Number of tasks successfully dispatched",
                example=1
            ),
            "tasks": ResponseField(
                field_type=list,
                description="List of tasks involved in this tick",
            ),
            "timestamp": ResponseField(
                field_type=str,
                description="Timestamp of the tick",
            )
        }
    ),
)
async def task_tick_endpoint(
    request: Request,
    agent_id: str,
    conversation_id: Optional[str] = Query(None, description="Optional conversation (session) ID to target"),
    dry_run: bool = Query(False, description="If True, only log what would be dispatched")
) -> Dict[str, Any]:
    """Trigger a proactive task dispatch check for an agent.

    This endpoint should be called periodically (e.g. every minute) by a
    scheduler like AWS EventBridge or a cron job.

    If conversation_id is provided, only that conversation will be checked,
    which is an optimized path for pushed-based task triggers.

    Supports authentication via admin token or API key.
    """
    dispatcher = await TaskDispatcher.find_one({
        "context.agent_id": agent_id,
        "context.enabled": True,
    })
    if not dispatcher:
         return {"error": f"TaskDispatcher action not found or enabled for agent {agent_id}"}

    result = await dispatcher.tick(dry_run=dry_run, conversation_id=conversation_id)

    if "error" in result:
        raise ResourceNotFoundError(result["error"])

    return result


@endpoint(
    "/proactive/webhooks/{agent_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Tasks"],
    response=success_response(
        data={
            "webhooks": ResponseField(
                field_type=list,
                description="List of proactive task related webhooks",
                example=[
                    {
                        "label": "Targeted Proactive Dispatch",
                        "type": "incoming",
                        "url": "https://...",
                    }
                ]
            )
        }
    )
)
async def list_scheduler_webhooks(
    agent_id: str,
    regenerate: bool = Query(False, description="Force-rotate the webhook API key")
) -> Dict[str, Any]:
    """List all proactive task-related webhooks for an agent.

    Includes the targeted dispatch URL (incoming) and the configured
    outgoing callback URL (if any).

    Use ?regenerate=true to rotate the API key and get a fresh dispatch URL.
    """
    from jvagent.core.agent import Agent

    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(f"Agent {agent_id} not found")

    scheduler = await agent.get_action_by_type("TaskCreationInteractAction")
    if not scheduler:
        return {"webhooks": []}

    webhooks = []

    # 1. Incoming: Targeted Tick URL (Dynamic) — always regenerate to clear stale paths
    try:
        dispatch_url = await scheduler.get_webhook_url(regenerate=regenerate)
        webhooks.append({
            "label": "Targeted Proactive Dispatch",
            "type": "incoming",
            "description": "POST to this URL to trigger an immediate check for a specific conversation.",
            "url": dispatch_url
        })
        logger.info(f"Proactive dispatch webhook URL for agent {agent_id}: {dispatch_url}")
    except Exception as e:
        logger.error(f"Failed to generate proactive dispatch webhook URL: {e}")

    # 2. Outgoing: Task-Creation Callback URL (Static Config)
    if scheduler.task_created_webhook_url:
        webhooks.append({
            "label": "Task-Creation Callback",
            "type": "outgoing",
            "description": "jvagent calls this URL whenever a new proactive task is scheduled.",
            "url": scheduler.task_created_webhook_url
        })

    return {"webhooks": webhooks}
