"""Search PageIndex documents via ActionResolver with directive formatting."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "search",
        "description": (
            "Search PageIndex documents using vectorless retrieval. "
            "Returns matching document sections with title, text, summary, "
            "source info, and a formatted directive string with numbered references."
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
                "include_references": {
                    "type": "boolean",
                    "description": "Whether to include numbered source references in the directive (default: true)",
                },
                "only_enabled": {
                    "type": "boolean",
                    "description": "Skip DocumentNodes with enabled=false (default: true)",
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
        if "doc_url" in r:
            entry["doc_url"] = r["doc_url"]
        if "start_index" in r:
            entry["start_index"] = r["start_index"]
        if "end_index" in r:
            entry["end_index"] = r["end_index"]
        tagged.append(entry)
    return tagged


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Search PageIndex documents by delegating to PageIndexAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return {"error": "PageIndexAction not found on this agent"}

    results = await action.search(
        query=arguments["query"],
        doc_name=arguments.get("doc_name"),
        strategy=arguments.get("strategy"),
        limit=arguments.get("limit"),
        collection_name=arguments.get("collection_name"),
        include_references=action.include_references,
        only_enabled=action.only_enabled,
        visitor=visitor,
    )

    directive = action.format_directive(results) if results else ""

    return {
        "results": _tag_results(results),
        "directive": directive,
    }
