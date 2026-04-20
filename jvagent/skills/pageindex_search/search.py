"""Search PageIndex documents via ActionResolver."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "search",
        "description": (
            "Search PageIndex documents using vectorless retrieval. "
            "Returns matching document sections with title, text, summary, and source info."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "doc_name": {
                    "type": "string",
                    "description": "Scope search to a single document by name",
                },
                "strategy": {
                    "type": "string",
                    "description": "Retrieval strategy: 'tree_search' (default), 'direct', or 'walker'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10)",
                },
                "collection_name": {
                    "type": "string",
                    "description": "Collection to search (default: agent's collection)",
                },
            },
            "required": ["query"],
        },
    }


def _tag_results(
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    tagged: List[Dict[str, Any]] = []
    for r in results:
        entry: Dict[str, Any] = {"provenance": "pageindex"}
        entry["title"] = r.get("title", "")
        entry["text"] = r.get("text", r.get("content", ""))
        entry["summary"] = r.get("summary", "")
        entry["doc_name"] = r.get("doc_name", "")
        entry["references"] = r.get("references", [])
        tagged.append(entry)
    return tagged


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    """Search PageIndex documents by delegating to PageIndexAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return [{"error": "PageIndexAction not found on this agent"}]

    results = await action.search(
        query=arguments["query"],
        doc_name=arguments.get("doc_name"),
        strategy=arguments.get("strategy"),
        limit=arguments.get("limit"),
        collection_name=arguments.get("collection_name"),
    )
    return _tag_results(results)
