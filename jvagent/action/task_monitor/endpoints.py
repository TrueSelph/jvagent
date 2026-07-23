import logging
from typing import Any, Dict, Optional

from fastapi import Query, Request
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.task_monitor.task_monitor import TaskMonitor

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
            "dispatched": ResponseField(
                field_type=int,
                description="Number of proactive tasks dispatched this tick",
                example=1,
            ),
            "timestamp": ResponseField(
                field_type=str,
                description="Timestamp of the tick",
            ),
        }
    ),
)
async def task_tick_endpoint(
    request: Request,
    agent_id: str,
    conversation_id: Optional[str] = Query(
        None, description="Optional conversation (session) ID to target"
    ),
    dry_run: bool = Query(
        False, description="If True, only log what would be dispatched"
    ),
) -> Dict[str, Any]:
    """Trigger a proactive task dispatch check for an agent."""
    monitor = await TaskMonitor.find_one(
        {
            "context.agent_id": agent_id,
            "context.enabled": True,
        }
    )
    if not monitor:
        raise ResourceNotFoundError(
            f"TaskMonitor action not found or enabled for agent {agent_id}"
        )

    result = await monitor.tick(dry_run=dry_run, conversation_id=conversation_id)

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
            )
        }
    ),
)
async def list_scheduler_webhooks(
    agent_id: str,
    regenerate: bool = Query(False, description="Force-rotate the webhook API key"),
) -> Dict[str, Any]:
    """List proactive task webhooks for an agent."""
    from jvagent.core.agent import Agent

    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(f"Agent {agent_id} not found")

    scheduler = await agent.get_action_by_type("TaskCreationInteractAction")
    if not scheduler:
        return {"webhooks": []}

    webhooks = []

    try:
        dispatch_url = await scheduler.get_webhook_url(regenerate=regenerate)
        webhooks.append(
            {
                "label": "Targeted Proactive Dispatch",
                "type": "incoming",
                "description": "GET this URL to trigger an immediate proactive task check.",
                "url": dispatch_url,
            }
        )
    except Exception as e:
        logger.error("Failed to generate proactive dispatch webhook URL: %s", e)

    if scheduler.task_created_webhook_url:
        webhooks.append(
            {
                "label": "Task-Creation Callback",
                "type": "outgoing",
                "description": "jvagent calls this URL when a new proactive task is scheduled.",
                "url": scheduler.task_created_webhook_url,
            }
        )

    return {"webhooks": webhooks}
