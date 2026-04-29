"""Delete a PageIndex document via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "delete_document",
        "description": "Delete a document and all its chunks from the PageIndex index.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_name": {
                    "type": "string",
                    "description": "Name of the document to delete",
                },
                "collection_name": {
                    "type": "string",
                    "description": "Collection the document belongs to (default: agent's collection)",
                },
            },
            "required": ["doc_name"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Delete a PageIndex document by delegating to PageIndexAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return {"error": "PageIndexAction not found on this agent"}

    result = await action.delete_document(
        doc_name=arguments["doc_name"],
        collection_name=arguments.get("collection_name"),
    )
    return {"deleted": result}
