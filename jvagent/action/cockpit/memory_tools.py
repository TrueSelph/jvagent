"""Memory harness tools for cockpit (read + write)."""

from typing import Any, List

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


def _build_memory_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose the Memory subsystem to the cockpit model."""

    async def _get_history(limit: int = 5, include_responses: bool = True) -> str:
        if not ctx.conversation:
            return "No conversation available."
        try:
            history = await ctx.conversation.get_interaction_history(
                limit=limit,
                with_utterance=True,
                with_response=include_responses,
                with_interpretation=False,
                with_event=False,
                formatted=False,
            )
        except Exception as exc:
            return f"Error retrieving history: {exc}"
        if not history:
            return "No prior interactions in this conversation."
        lines = []
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if content:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    async def _get_user_info() -> str:
        if not ctx.user_id or not ctx.agent:
            return "No authenticated user."
        try:
            memory = await ctx.agent.get_memory()
            if not memory:
                return "Memory subsystem unavailable."
            user_node = await memory.get_user(ctx.user_id)
            if not user_node:
                return f"User '{ctx.user_id}' not found."
            name = getattr(user_node, "name", "Unknown")
            usage = getattr(user_node, "usage", {}) or {}
            interaction_count = getattr(user_node, "interaction_count", 0)
            return (
                f"User: {name} (id: {ctx.user_id})\n"
                f"Interactions: {interaction_count}\n"
                f"Usage: {usage}"
            )
        except Exception as exc:
            return f"Error: {exc}"

    async def _update_user_model(key: str, value: str) -> str:
        """Store or update a key-value pair in the user model (facts/preferences)."""
        if not ctx.user_id or not ctx.agent:
            return "Error: no authenticated user."
        try:
            memory = await ctx.agent.get_memory()
            if not memory:
                return "Error: memory subsystem unavailable."
            user_node = await memory.get_user(ctx.user_id)
            if not user_node:
                return f"Error: user '{ctx.user_id}' not found."
            user_model = getattr(user_node, "user_model", None)
            if not isinstance(user_model, dict):
                user_model = {}
                user_node.user_model = user_model
            if key.startswith("preference."):
                pref_key = key[len("preference.") :]
                prefs = user_model.get("preferences", {})
                prefs[pref_key] = value
                user_model["preferences"] = prefs
            else:
                facts = user_model.get("facts", [])
                facts.append(f"{key}: {value}")
                user_model["facts"] = facts
            await user_node.save()
            return f"Updated user model: {key} = {value}"
        except Exception as exc:
            return f"Error updating user model: {exc}"

    async def _set_preference(key: str, value: str) -> str:
        """Store or update a user preference for this conversation."""
        if not ctx.conversation:
            return "Error: no conversation available."
        try:
            context = getattr(ctx.conversation, "context", None)
            if context is None:
                return "Error: conversation has no context."
            prefs = context.get("preferences", {})
            prefs[key] = value
            context["preferences"] = prefs
            await ctx.conversation.save()
            return f"Preference set: {key} = {value}"
        except Exception as exc:
            return f"Error setting preference: {exc}"

    return [
        Tool(
            name="memory_get_history",
            description=(
                "Retrieve recent interactions from this conversation. "
                "Use limit to control how many past exchanges to fetch."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of past interactions to retrieve (default 5).",
                        "default": 5,
                    },
                    "include_responses": {
                        "type": "boolean",
                        "description": "Whether to include assistant responses (default true).",
                        "default": True,
                    },
                },
            },
            execute=_get_history,
        ),
        Tool(
            name="memory_get_user_info",
            description=(
                "Get information about the current user, including name, "
                "interaction count, and usage statistics."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_get_user_info,
        ),
        Tool(
            name="memory_update_user_model",
            description=(
                "Store or update a key-value pair in the user model. "
                "Use keys starting with 'preference.' to store preferences, "
                "or any other key to store facts about the user. "
                "Examples: preference.language=French, preference.timezone=EST, name=Alice"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The key to set. Use 'preference.X' for preferences, or any string for facts.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value to store.",
                    },
                },
                "required": ["key", "value"],
            },
            execute=_update_user_model,
        ),
        Tool(
            name="memory_set_preference",
            description=(
                "Store or update a user preference for the current conversation. "
                "Preferences persist across interactions within this conversation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Preference key (e.g., 'language', 'tone', 'format').",
                    },
                    "value": {
                        "type": "string",
                        "description": "Preference value.",
                    },
                },
                "required": ["key", "value"],
            },
            execute=_set_preference,
        ),
    ]
