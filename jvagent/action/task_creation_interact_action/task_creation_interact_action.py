import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import uuid

from jvspatial.core.annotations import attribute
from jvagent.action.interact.base import InteractAction
from jvagent.action.task_creation_interact_action.prompts import TASK_SCHEDULER_PROMPT
from jvagent.core.app import App

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class TaskCreationInteractAction(InteractAction):
    """Proactive task scheduler.

    Runs after the response is generated (weight 200) to identify and schedule
    future follow-up tasks based on the complete interaction outcome.
    This action is designed to be decoupled from the response latency.
    """

    weight: int = attribute(
        default=200,
        description="Runs late to avoid blocking response delivery.",
    )
    always_execute: bool = attribute(
        default=True,
        description="Always execute to evaluate if follow-ups are needed after each turn.",
    )
    run_in_background: bool = attribute(
        default=True,
        description="Run in the background after the response is sent to avoid typing indicators.",
    )
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use.",
    )
    model: str = attribute(
        default="gpt-4o-mini",
        description="Model identifier (fast model is fine for task extraction).",
    )
    capabilities_cache_ttl_seconds: int = attribute(
        default=300,
        description="Cache TTL for capabilities string.",
    )
    skip_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR"],
        description="Skip proactive scheduling for these intent types.",
    )
    task_created_webhook_url: Optional[str] = attribute(
        default=None,
        description="Optional webhook URL to notify when a proactive task is created or updated.",
    )
    webhook_url: Optional[str] = attribute(
        default=None,
        description="Proactive task dispatch webhook URL (auto-generated if not provided).",
    )
    webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="ID of the API key used for the proactive dispatch webhook.",
    )

    async def get_webhook_url(self, regenerate: bool = False) -> str:
        """Generate or retrieve secure task-dispatch webhook URL with API key authentication."""
        from jvagent.core.public_url import get_public_base_url
        from jvspatial.api.auth.api_key_service import APIKeyService
        from jvspatial.db import get_prime_database
        from jvspatial.core.context import GraphContext
        from jvagent.action.utils.webhook_system_user import get_or_create_system_user_for_webhook
        from jvspatial.exceptions import ValidationError

        base_url = get_public_base_url()
        if not base_url or not base_url.strip():
            raise ValidationError(
                "base_url (JVAGENT_PUBLIC_BASE_URL) is required for webhook URL generation"
            )

        agent = await self.get_agent()
        agent_id = str(agent.id)
        expected_url_base = f"{base_url}/api/proactive/tick/{agent_id}"

        try:
            prime_db = get_prime_database()
            context = GraphContext(database=prime_db)
            api_key_service = APIKeyService(context=context)

            # Reuse existing URL if valid
            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                return self.webhook_url

            system_user_id = await get_or_create_system_user_for_webhook(
                "proactive-task-service@system.internal", "webhook:proactive"
            )

            if regenerate and self.webhook_api_key_id:
                try:
                    from jvspatial.api.auth.models import APIKey
                    old_key = await context.get(APIKey, self.webhook_api_key_id)
                    if old_key:
                        old_key.is_active = False
                        old_key._graph_context = context
                        await context.save(old_key)
                except Exception:
                    pass

            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"Proactive Task Dispatch - {agent.name}",
                permissions=["webhook:proactive"],
                expires_in_days=None,
                allowed_ips=[],
                allowed_endpoints=["/api/proactive/tick/"],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"

            # Persist the dynamic URL attributes
            self._graph_context = context
            await self.save()
            return self.webhook_url

        except Exception as e:
            logger.error(f"Failed to generate proactive webhook URL: {e}", exc_info=True)
            raise ValidationError(f"Webhook URL generation failed: {e}")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Analyze the finished interaction and schedule future proactive tasks."""
        interaction = visitor.interaction
        if not interaction:
            return

        conversation = getattr(visitor, "conversation", None) or await interaction.get_conversation()
        if not conversation:
            return

        app = await App.get()
        now = await app.now() if app else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        current_time_str = now.strftime("%Y-%m-%d %H:%M")

        # Include history + Current response!
        # Handle cases where conversation might be returned as a generic Node (AttributeError protection)
        if hasattr(conversation, "get_interaction_history"):
            history = await conversation.get_interaction_history(limit=5, formatted=True)
        else:
            # Fallback for generic Nodes
            raw_history = await conversation.nodes(direction="out", limit=5)
            # Re-traverse to ensure chronological order and format manually if needed
            # For now, we'll try a simple format or assume missing history is okay for extraction
            history = [] 

        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history])

        capabilities_str = await self._get_capabilities()

        try:
            # Robust task retrieval
            active_tasks = []
            if hasattr(conversation, "get_active_tasks"):
                active_tasks = conversation.get_active_tasks(status="active")
            elif hasattr(conversation, "active_tasks"):
                active_tasks = [t for t in conversation.active_tasks if t.get("status") == "active"]

            pending_tasks_section = "No pending tasks."
            if active_tasks:
                pending_tasks_section = "\n".join(
                    [f"ID: {t.get('task_id')} | TASK: {t.get('description')}"
                     for t in active_tasks if t.get("task_type") == "PROACTIVE"]
                )

            model_action = await self.get_model_action(required=True)

            # Updated prompt context: explicitly mention Interpretation and Response
            prompt_content = TASK_SCHEDULER_PROMPT.format(
                current_time=current_time_str,
                capabilities=capabilities_str,
                recent_history=history_str,
                user_input=f"INTENT: {interaction.interpretation}\nUTTERANCE: {interaction.utterance}",
                pending_tasks_section=pending_tasks_section
            )

            # # Additional context about the agent's actual reply
            # prompt_content += f"\n\nWHAT THE AGENT JUST REPLIED:\n{interaction.response}"

            response = await model_action.generate(
                prompt=prompt_content,
                model=self.model,
                calling_action_name=self.get_class_name(),
                interaction=interaction,
            )

            if response and "NO_TASKS" not in response:
                # 1. Process Completions
                completions = re.findall(r"COMPLETE_TASK:\s*([a-fA-F0-9\-]+|[0-9]+)", response)
                tasks = getattr(conversation, "active_tasks", [])
                modified = False
                for task_id in completions:
                    for t in tasks:
                        if t.get("task_id") == task_id:
                            t["status"] = "completed"
                            t["updated_at"] = datetime.now(timezone.utc).isoformat()
                            modified = True
                if modified:
                    await conversation.save()

                # 2. Process New Tasks
                new_tasks = self._extract_tasks(response)
                for task in new_tasks:
                    new_task_id = f"task_{uuid.uuid4().hex}"
                    tasks.append({
                        "task_id": new_task_id,
                        "description": task["description"],
                        "task_type": "PROACTIVE",
                        "status": "active",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "metadata": {
                            "trigger_time": task["trigger_time"],
                            "trigger_condition": task["trigger_condition"],
                            "context": task["context"],
                            "channel": interaction.channel or "default",
                        }
                    })
                if new_tasks:
                    await conversation.save()
                    
                for task in new_tasks:
                    logger.info(f"TaskCreation: Successfully ADDED task '{task['description']}' for session {conversation.session_id} @ {task['trigger_time']}")

        except Exception as e:
            logger.error(f"BrainSchedulerAction: Error: {e}", exc_info=True)

    async def _get_capabilities(self) -> str:
        agent = await self.get_agent()
        if not agent:
            return "No capabilities."
        return await self.get_agent_capabilities_brief(
            agent, ttl_seconds=self.capabilities_cache_ttl_seconds
        )

    def _extract_tasks(self, response: str) -> List[Dict[str, Any]]:
        tasks = []
        content = response.replace("\r\n", "\n")
        pattern = (
            r"TASK:[ \t]*(?P<desc>.*?)\s*"
            r"TRIGGER_TIME:[ \t]*(?P<time>.*?)\s*"
            r"TRIGGER_CONDITION:[ \t]*(?P<cond>.*?)\s*"
            r"CONTEXT:[ \t]*(?P<ctx>.*?)\s*"
            r"(?=\n+TASK:|$)"
        )

        for match in re.finditer(pattern, content, re.DOTALL | re.MULTILINE):
            raw_time_str = match.group("time").strip()
            normalized_time_str = raw_time_str.replace(" ", "T")
            tasks.append({
                "description": match.group("desc").strip(),
                "trigger_time": normalized_time_str,
                "trigger_condition": match.group("cond").strip(),
                "context": match.group("ctx").strip(),
            })
        return tasks
