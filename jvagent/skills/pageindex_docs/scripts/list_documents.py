"""List PageIndex documents via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_documents",
        "description": "List documents in the PageIndex index, optionally filtered by collection and metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_name": {
                    "type": "string",
                    "description": "Collection to list (default: agent's collection)",
                },
                "metadata_filter": {
                    "type": "object",
                    "description": "Key-value metadata filter to narrow results",
                },
            },
            "required": [],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    """List PageIndex documents by delegating to PageIndexAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return [{"error": "PageIndexAction not found on this agent"}]

    return await action.list_documents(
        collection_name=arguments.get("collection_name"),
        metadata_filter=arguments.get("metadata_filter"),
    )
