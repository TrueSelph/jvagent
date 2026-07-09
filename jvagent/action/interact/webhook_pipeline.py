"""Channel-neutral webhook interact pipeline helpers.

Public API for channel adapters (WhatsApp, email, Messenger) to finalize
interactions without importing private symbols from ``interact.endpoints``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial import flush_deferred_entities
from jvspatial.exceptions import DatabaseError

from jvagent.action.interact.conversation_lock_manager import ConversationLockManager
from jvagent.core.app import App
from jvagent.logging.service import INTERACTION_LEVEL_NUMBER
from jvagent.memory.conversation import Conversation

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

_conversation_lock_manager = ConversationLockManager()

_TRUNCATE_LEN = 200


async def finalize_usage(interaction: Any) -> None:
    """Compute usage from observability_metrics and update user stats."""
    if not interaction:
        return
    if hasattr(interaction, "compute_usage"):
        interaction.compute_usage()
        await interaction.save()
    await flush_deferred_entities(interaction, strict=False)
    usage = getattr(interaction, "usage", None)
    if usage:
        try:
            user = await interaction.get_user()
            if user and hasattr(user, "add_usage_from_interaction"):
                await user.add_usage_from_interaction(usage)
        except Exception as e:
            logger.warning(
                "Failed to update user usage stats: interaction_id=%s user_id=%s error=%s",
                getattr(interaction, "id", None),
                getattr(interaction, "user_id", None),
                e,
            )


async def run_background_actions(walker: InteractWalker) -> None:
    """Execute deferred background InteractActions after the interaction is closed."""
    if not walker.background_actions:
        return

    from jvagent.action.model.context import set_interaction

    bg_interaction = getattr(walker, "interaction", None)
    set_interaction(bg_interaction)
    try:
        await _run_background_actions_inner(walker)
    finally:
        set_interaction(None)
        if bg_interaction is not None:
            try:
                await finalize_usage(bg_interaction)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to finalize usage after background actions: %s", e
                )


async def _run_background_actions_inner(walker: InteractWalker) -> None:
    for action in walker.background_actions:
        action_name = (
            action.get_class_name()
            if hasattr(action, "get_class_name")
            else action.__class__.__name__
        )
        try:
            access_ok = await walker.enforce_interact_action_access(
                action, stage="background"
            )
        except Exception:
            logger.error(
                "Access check failed for background action %s; denying execution",
                action_name,
                exc_info=True,
                extra={
                    "agent_id": getattr(action, "agent_id", None),
                    "action_class": action.__class__.__name__,
                    "context": "background_access_check",
                },
            )
            continue
        if not access_ok:
            continue

        try:
            logger.debug("Running background action: %s", action_name)
            walker._current_action = action
            walker._skip_current_action_record = False
            await action.execute(walker)
            logger.debug("Background action completed: %s", action_name)
        except Exception as e:
            agent_id = getattr(action, "agent_id", None)
            interaction_id = walker.interaction.id if walker.interaction else None
            logger.error(
                "Error in background action %s: %s",
                getattr(action, "label", action.__class__.__name__),
                e,
                exc_info=True,
                extra={
                    "agent_id": agent_id,
                    "interaction_id": interaction_id,
                    "action_class": action.__class__.__name__,
                },
            )
        finally:
            walker._current_action = None
            walker._skip_current_action_record = False


def sanitize_visitor_data_for_log(visitor_data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize visitor.data for safe logging (no PII bloat, no media/base64)."""
    if not visitor_data:
        return {}
    out: Dict[str, Any] = {}
    for key, val in visitor_data.items():
        if key == "whatsapp_payload" and isinstance(val, dict):
            payload = {}
            for pk, pv in val.items():
                if pk == "media":
                    payload[pk] = "<media>"
                elif pk == "quoted_message":
                    payload[pk] = "<quoted_message>"
                elif pk in ("body", "caption") and isinstance(pv, str):
                    payload[pk] = (
                        pv[:_TRUNCATE_LEN] + "..." if len(pv) > _TRUNCATE_LEN else pv
                    )
                else:
                    payload[pk] = pv
            out[key] = payload
        elif key == "whatsapp_media" and isinstance(val, list):
            out[key] = [{"type": "media", "count": len(val)}]
        else:
            out[key] = val
    return out


def build_interaction_log_data(
    interaction,
    app_id,
    agent_id=None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    visitor_data: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    """Build log payload for INTERACTION-level logging."""
    interaction_id = interaction.id if hasattr(interaction, "id") else None
    user_id = interaction.user_id if hasattr(interaction, "user_id") else ""
    session_id = interaction.session_id if hasattr(interaction, "session_id") else ""
    conversation_id = (
        interaction.conversation_id if hasattr(interaction, "conversation_id") else ""
    )
    utterance = interaction.utterance if hasattr(interaction, "utterance") else ""
    response = interaction.response if hasattr(interaction, "response") else None
    channel = interaction.channel if hasattr(interaction, "channel") else "default"
    interpretation = (
        interaction.interpretation if hasattr(interaction, "interpretation") else None
    )
    anchors = interaction.anchors if hasattr(interaction, "anchors") else []
    actions = interaction.actions if hasattr(interaction, "actions") else []
    directives = interaction.directives if hasattr(interaction, "directives") else []
    parameters = interaction.parameters if hasattr(interaction, "parameters") else []
    events = interaction.events if hasattr(interaction, "events") else []
    observability_metrics = (
        interaction.observability_metrics
        if hasattr(interaction, "observability_metrics")
        else []
    )
    streamed = interaction.streamed if hasattr(interaction, "streamed") else False
    closed = interaction.closed if hasattr(interaction, "closed") else False
    usage = getattr(interaction, "usage", None) or {}
    started_at = (
        interaction.started_at.isoformat()
        if hasattr(interaction, "started_at") and interaction.started_at
        else None
    )
    completed_at = (
        interaction.completed_at.isoformat()
        if hasattr(interaction, "completed_at") and interaction.completed_at
        else None
    )

    if hasattr(interaction, "get_state"):
        interaction_data = interaction.get_state()
    else:
        interaction_data = {
            "id": interaction_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "session_id": session_id,
            "utterance": utterance,
            "channel": channel,
            "response": response,
            "actions": actions,
            "directives": directives,
            "parameters": parameters,
            "events": events,
            "observability_metrics": observability_metrics,
            "usage": usage,
            "interpretation": interpretation,
            "anchors": anchors,
            "started_at": started_at,
            "completed_at": completed_at,
            "closed": closed,
            "streamed": streamed,
        }

    message = (
        f"Interaction: {utterance[:100]}" if utterance else "Interaction completed"
    )
    if response:
        message += f" → {response[:100]}"

    event_code = "interaction_completed"
    if closed:
        event_code = "interaction_closed"

    duration = None
    if hasattr(interaction, "get_duration"):
        duration = interaction.get_duration()
        if duration <= 0:
            duration = None

    log_data = {
        "event_code": event_code,
        "app_id": app_id,
        "agent_id": agent_id or "",
        "user_id": user_id,
        "session_id": session_id,
        "interaction_id": interaction_id or "",
        "conversation_id": conversation_id,
        "interaction_data": interaction_data,
        "utterance": utterance,
        "response": response,
        "channel": channel,
        "actions": actions,
        "directives": directives,
        "parameters": parameters,
        "events": events,
        "tasks": tasks if tasks is not None else [],
        "interpretation": interpretation,
        "anchors": anchors,
        "streamed": streamed,
        "closed": closed,
        "has_response": response is not None,
        "action_count": len(actions),
        "started_at": started_at,
        "completed_at": completed_at,
    }

    if duration is not None:
        log_data["duration_seconds"] = duration

    if visitor_data:
        log_data["interact_data"] = sanitize_visitor_data_for_log(visitor_data)

    return log_data, message


async def emit_interaction_log(
    walker: InteractWalker, interaction: Any, agent_id: Optional[str]
) -> None:
    """Emit the INTERACTION-level log entry for a completed turn."""
    try:
        from jvagent.action.interact.response_builder import (
            _consolidated_tasks_for_interaction,
        )

        app = await App.get()
        app_id = app.id if app else ""
        tasks: List[Dict[str, Any]] = []
        if walker.conversation:
            active = walker.conversation.get_tasks(status="active")
            tasks = _consolidated_tasks_for_interaction(
                interaction, walker.conversation, active
            )
        log_data, message = build_interaction_log_data(
            interaction,
            app_id,
            agent_id,
            tasks=tasks,
            visitor_data=walker.data,
        )
        logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
    except Exception as e:
        logger.warning("Failed to log interaction: %s", e)


def _extract_quoted_text(quoted_message: Optional[Dict[str, Any]]) -> Optional[str]:
    if not quoted_message or not isinstance(quoted_message, dict):
        return None
    for key in ("body", "content", "text"):
        val = quoted_message.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    nested = quoted_message.get("message") or {}
    if isinstance(nested, dict):
        for key in ("body", "content", "text"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def build_utterance_with_quoted_context(
    quoted_message: Optional[Dict[str, Any]],
    base_utterance: Optional[str],
) -> Optional[str]:
    """Augment utterance with quoted message context when user replies to a message."""
    quoted_text = _extract_quoted_text(quoted_message)
    if not quoted_text:
        return base_utterance
    user_utterance = (
        base_utterance.strip() if base_utterance else "(no additional message)"
    )
    return f'User replied to: "{quoted_text}" with "{user_utterance}"'


async def get_conversation_with_lock(
    sender: str, agent_id: Optional[str] = None
) -> Optional[Any]:
    """Get conversation for user with locking to prevent duplicate creation."""
    lock = await _conversation_lock_manager.acquire_lock(sender)

    async with lock:
        try:
            if agent_id:
                from jvagent.core.agent import Agent
                from jvagent.memory.user import User

                agent = await Agent.get(agent_id)
                if agent:
                    memory = await agent.get_memory()
                    if memory:
                        for user in await memory.nodes(node=User, user_id=sender):
                            active = await user.get_active_conversation()
                            if active:
                                return active
                return None
            return await Conversation.find_one({"context.user_id": sender})
        except DatabaseError as e:
            logger.error(
                "Database error finding conversation for user %s: %s", sender, e
            )
            return None


async def finalize_interaction_from_webhook(
    walker: InteractWalker,
    agent_id: str,
    sender: str,
) -> None:
    """Close interaction, flush, run background actions, usage, and log."""
    interaction = walker.interaction
    if not interaction:
        return

    try:
        await interaction.close_interaction()
        await flush_deferred_entities(interaction, walker.conversation, strict=True)
        await run_background_actions(walker)
        await finalize_usage(interaction)
        await emit_interaction_log(walker, interaction, agent_id)
    except DatabaseError as e:
        logger.error("Database error finalizing interaction for user %s: %s", sender, e)
        raise
    except Exception as e:
        logger.error("Error finalizing interaction for user %s: %s", sender, e)


__all__ = [
    "build_interaction_log_data",
    "build_utterance_with_quoted_context",
    "emit_interaction_log",
    "finalize_interaction_from_webhook",
    "finalize_usage",
    "get_conversation_with_lock",
    "run_background_actions",
    "sanitize_visitor_data_for_log",
]
