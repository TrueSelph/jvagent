import logging
import httpx
from typing import Any, Dict
from jvspatial import create_task

logger = logging.getLogger(__name__)

async def trigger_task_created_callback(conversation: Any, task_entry: Dict[str, Any]) -> None:
    """Fire a webhook callback whenever a proactive task is created or updated as active.
    
    This follows the pattern of push-based task triggers to avoid global database polling.
    """
    try:
        # 1. Get Agent
        agent = await conversation.get_agent()
        if not agent:
            return

        # 2. Extract Webhook URL
        # We look for 'task_created_webhook_url' in TaskCreationInteractAction
        # or in agent metadata/env.
        webhook_url = None
        
        # Check environment variable first for global default
        import os
        webhook_url = os.environ.get("JVAGENT_TASK_CREATED_WEBHOOK_URL")

        # Check action config (more specific)
        dispatch_url = None
        try:
            from jvagent.action.task_creation_interact_action.task_creation_interact_action import TaskCreationInteractAction
            scheduler = await agent.get_action_by_type("TaskCreationInteractAction")
            if scheduler:
                if hasattr(scheduler, "task_created_webhook_url") and scheduler.task_created_webhook_url:
                    webhook_url = scheduler.task_created_webhook_url
                
                # Retrieve the secure dynamic URL for dispatching
                try:
                    # Resolve base dispatch URL (agent-wide)
                    base_dispatch_url = await scheduler.get_webhook_url()
                    if base_dispatch_url:
                        # Enhance URL with session-specific targeting
                        session_id = str(conversation.session_id)
                        connector = "&" if "?" in base_dispatch_url else "?"
                        dispatch_url = f"{base_dispatch_url}{connector}conversation_id={session_id}"
                except Exception:
                    pass
        except Exception:
            pass

        if not webhook_url:
            return

        # 3. Fire Webhook (Background)
        async def _fire():
            try:
                payload = {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation.session_id),
                    "task_id": task_entry.get("task_id"),
                    "task_type": task_entry.get("task_type"),
                    "description": task_entry.get("description"),
                    "next_trigger_at": task_entry.get("next_trigger_at"),
                    "dispatch_url": dispatch_url,  # SESSION-TARGETED URL to call back
                    "metadata": task_entry.get("metadata", {}),
                    "timestamp": task_entry.get("created_at"),
                    "event": "task_created"
                }
                
                logger.info(
                    f"Callback: Firing task-created webhook for task {task_entry.get('task_id')} "
                    f"(Session: {conversation.session_id}) to {webhook_url}. "
                    f"Session-Targeted Dispatch URL: {dispatch_url}"
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(webhook_url, json=payload)
                    response.raise_for_status()
                    logger.debug(f"Webhook response: {response.status_code}")
            except Exception as e:
                logger.error(f"Failed to fire task creation webhook: {e}")

        # Use jvspatial.create_task to fire-and-forget safely in background
        await create_task(_fire(), name=f"task_webhook_{task_entry.get('task_id')}")

    except Exception as e:
        logger.error(f"Error in trigger_task_created_callback: {e}")
