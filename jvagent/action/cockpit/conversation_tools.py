"""Conversation harness tools for cockpit."""

from typing import Any, List

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


def _build_conversation_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose conversation search to the cockpit model."""

    async def _search(keyword: str, limit: int = 5) -> str:
        if not ctx.conversation:
            return "Error: no conversation available."
        try:
            # formatted=True returns {role, content} pairs we can read directly.
            history = await ctx.conversation.get_interaction_history(
                limit=50,
                with_utterance=True,
                with_response=True,
                with_interpretation=False,
                with_event=False,
                formatted=True,
            )
        except Exception as exc:
            return f"Error: {exc}"

        matches = []
        kw = keyword.lower()
        for entry in history:
            content = entry.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if kw in content.lower():
                matches.append(f"[{entry.get('role','')}] {content[:300]}")
            if len(matches) >= limit:
                break

        if not matches:
            return f'No conversation entries found matching "{keyword}".'
        return f'Found {len(matches)} match(es) for "{keyword}":\n' + "\n---\n".join(
            matches
        )

    async def _summarize(limit: int = 10) -> str:
        if not ctx.conversation:
            return "Error: no conversation available."
        try:
            history = await ctx.conversation.get_interaction_history(
                limit=limit,
                with_utterance=True,
                with_response=True,
                with_interpretation=False,
                with_event=False,
                formatted=True,
            )
        except Exception as exc:
            return f"Error: {exc}"

        if not history:
            return "No conversation history to summarize."
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
                lines.append(f"[{role}] {content[:200]}")
        return f"Recent conversation ({len(history)} exchanges):\n" + "\n".join(lines)

    return [
        Tool(
            name="conversation_search",
            description="Search conversation history for messages containing a keyword.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Keyword to search for in conversation history.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["keyword"],
            },
            execute=_search,
        ),
        Tool(
            name="conversation_summarize",
            description="Get a brief summary of recent conversation exchanges.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent exchanges to summarize (default 10).",
                        "default": 10,
                    },
                },
            },
            execute=_summarize,
        ),
    ]
