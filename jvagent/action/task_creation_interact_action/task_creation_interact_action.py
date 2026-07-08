import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.task_creation_interact_action.prompts import TASK_SCHEDULER_PROMPT
from jvagent.core.app import App, app_now_aware_utc
from jvagent.memory.task_proactive import ProactiveTaskSpec, coerce_priority

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
        from jvspatial.api.auth.api_key_service import APIKeyService
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_prime_database
        from jvspatial.exceptions import ValidationError

        from jvagent.action.utils.webhook_system_user import (
            get_or_create_system_user_for_webhook,
        )
        from jvagent.core.public_url import get_public_base_url

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
            logger.error(
                f"Failed to generate proactive webhook URL: {e}", exc_info=True
            )
            raise ValidationError(f"Webhook URL generation failed: {e}")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Analyze the finished interaction and schedule future proactive tasks."""
        interaction = visitor.interaction
        if not interaction:
            return

        conversation = (
            getattr(visitor, "conversation", None)
            or await interaction.get_conversation()
        )
        if not conversation:
            return

        app = await App.get()
        now = await app_now_aware_utc(app)

        current_time_str = now.strftime("%Y-%m-%d %H:%M")

        # Include history + Current response!
        # Handle cases where conversation might be returned as a generic Node (AttributeError protection)
        if hasattr(conversation, "get_interaction_history"):
            history = await conversation.get_interaction_history(
                limit=5, formatted=True
            )
        else:
            # Fallback for generic Nodes: assume missing history is okay for
            # extraction rather than re-traversing for chronological order.
            history = []

        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history])

        capabilities_str = await self._get_capabilities()

        try:
            # Robust task retrieval
            pending_handles = visitor.tasks.list_queue(statuses=("pending", "active"))
            pending_tasks_section = "No pending tasks."
            if pending_handles:
                pending_tasks_section = "\n".join(
                    [
                        f"ID: {h.id} | TASK: {h.title} | STATUS: {h.status}"
                        for h in pending_handles
                    ]
                )

            model_action = await self.get_model_action(required=True)

            # Updated prompt context: explicitly mention Interpretation and Response
            prompt_content = TASK_SCHEDULER_PROMPT.format(
                current_time=current_time_str,
                capabilities=capabilities_str,
                recent_history=history_str,
                user_input=f"INTENT: {interaction.interpretation}\nUTTERANCE: {interaction.utterance}",
                pending_tasks_section=pending_tasks_section,
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
                completions = re.findall(
                    r"COMPLETE_TASK:\s*([a-fA-F0-9\-]+|[0-9]+)", response
                )
                for task_id in completions:
                    t = visitor.tasks.get(task_id)
                    if t:
                        await t.complete()

                # 2. Process New Tasks
                new_tasks = self._extract_tasks(response)
                for task in new_tasks:
                    spec = self._to_proactive_spec(task, interaction)
                    await visitor.tasks.enqueue_proactive(
                        spec,
                        owner_action=self.get_class_name(),
                        title=task["description"],
                    )
                    logger.info(
                        "TaskCreation: queued proactive task '%s' for session %s "
                        "(not_before=%s, trigger_on=%s)",
                        task["description"],
                        conversation.session_id,
                        spec.not_before,
                        spec.trigger_on,
                    )

        except Exception as e:
            logger.error(f"BrainSchedulerAction: Error: {e}", exc_info=True)

    async def _get_capabilities(self) -> str:
        """Render the agent's advertised abilities as a bullet list for the
        proactive-scheduler prompt.

        Aggregation lives on ``Agent.collect_capabilities()`` (the single source
        of truth); this only formats. Freshness is handled at the agent layer —
        ``Agent.save()`` invalidates the action caches — so no bespoke TTL cache
        is needed here.
        """
        agent = await self.get_agent()
        if not agent:
            return "No specific capabilities registered."
        caps = await agent.collect_capabilities()
        if not caps:
            return "No specific capabilities registered."
        return "\n".join(f"- {c}" for c in caps)

    def _extract_tasks(self, response: str) -> List[Dict[str, Any]]:
        tasks = []
        content = response.replace("\r\n", "\n")
        pattern = (
            r"TASK:[ \t]*(?P<desc>.*?)\s*"
            r"(?:NOT_BEFORE|TRIGGER_TIME):[ \t]*(?P<time>.*?)\s*"
            r"(?:TRIGGER_ON:[ \t]*(?P<trigger_on>.*?)\s*)?"
            r"(?:TRIGGER_KEYWORD:[ \t]*(?P<keyword>.*?)\s*)?"
            r"(?:TRIGGER_MOOD:[ \t]*(?P<mood>.*?)\s*)?"
            r"(?:TRIGGER_CONDITION:[ \t]*(?P<cond>.*?)\s*)?"
            r"CONTEXT:[ \t]*(?P<ctx>.*?)\s*"
            r"(?:PRIORITY:[ \t]*(?P<priority>.*?)\s*)?"
            r"(?=\n+TASK:|$)"
        )

        for match in re.finditer(pattern, content, re.DOTALL | re.MULTILINE):
            raw_time_str = match.group("time").strip()
            normalized_time_str = raw_time_str.replace(" ", "T")
            tasks.append(
                {
                    "description": match.group("desc").strip(),
                    "not_before": normalized_time_str or None,
                    "trigger_on": (match.group("trigger_on") or "").strip(),
                    "trigger_keyword": (match.group("keyword") or "").strip(),
                    "trigger_mood": (match.group("mood") or "").strip(),
                    "trigger_condition": (match.group("cond") or "").strip(),
                    "context": match.group("ctx").strip(),
                    "priority": (match.group("priority") or "").strip(),
                }
            )
        return tasks

    def _to_proactive_spec(
        self,
        task: Dict[str, Any],
        interaction: Any,
    ) -> ProactiveTaskSpec:
        trigger_on = (task.get("trigger_on") or "").strip().lower()
        trigger_keyword = (task.get("trigger_keyword") or "").strip()
        trigger_mood = (task.get("trigger_mood") or "").strip()
        legacy_cond = (task.get("trigger_condition") or "").strip().lower()

        if not trigger_on:
            if legacy_cond in ("none", ""):
                trigger_on = "schedule"
            elif legacy_cond in (
                "schedule",
                "user_message",
                "keyword",
                "mood",
                "any",
            ):
                trigger_on = legacy_cond
            else:
                trigger_on = "keyword"
                trigger_keyword = legacy_cond

        if trigger_keyword.lower() in ("none", ""):
            trigger_keyword = None
        if trigger_mood.lower() in ("none", ""):
            trigger_mood = None

        priority = coerce_priority(task.get("priority"))

        return ProactiveTaskSpec(
            directive=task["description"],
            context=task.get("context", ""),
            channel=interaction.channel or "default",
            not_before=task.get("not_before"),
            trigger_on=trigger_on,  # type: ignore[arg-type]
            trigger_keyword=trigger_keyword,
            trigger_mood=trigger_mood,
            priority=priority,
        )
