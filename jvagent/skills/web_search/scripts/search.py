"""Search the web via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "search",
        "description": "Search the web and return a list of results with title, link, and snippet.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string",
                },
            },
            "required": ["query"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, str]]:
    """Search the web by delegating to SerperWebSearchAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("SerperWebSearchAction")
    if action is None:
        return [{"error": "SerperWebSearchAction not found on this agent"}]

    return await action.search(query=arguments["query"])
