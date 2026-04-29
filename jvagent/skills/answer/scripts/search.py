"""Composed search tool: PageIndex (internal KB) + Web search with provenance tagging."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "search",
        "description": (
            "Search for information using a retrieval cascade. "
            "source='pageindex' searches the internal knowledge base (default, always try first). "
            "source='web' searches the public web for current/supplemental information. "
            "source='all' searches both and merges results. "
            "Each result is tagged with provenance for citation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string",
                },
                "source": {
                    "type": "string",
                    "enum": ["pageindex", "web", "all"],
                    "description": (
                        "Which source(s) to search: 'pageindex' (internal KB, default), "
                        "'web' (public web), or 'all' (both)"
                    ),
                },
                "doc_name": {
                    "type": "string",
                    "description": "Scope PageIndex search to a single document by name",
                },
                "strategy": {
                    "type": "string",
                    "description": "PageIndex retrieval strategy: 'tree_search' (default), 'direct', or 'walker'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results per source (default: 10)",
                },
                "collection_name": {
                    "type": "string",
                    "description": "PageIndex collection to search (default: agent's collection)",
                },
            },
            "required": ["query"],
        },
    }


def _tag_pageindex_results(
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


def _tag_web_results(
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    tagged: List[Dict[str, Any]] = []
    for r in results:
        entry: Dict[str, Any] = {"provenance": "web"}
        entry["title"] = r.get("title", "")
        entry["link"] = r.get("link", "")
        entry["snippet"] = r.get("snippet", "")
        tagged.append(entry)
    return tagged


async def _search_pageindex(
    resolver: Any,
    query: str,
    doc_name: Optional[str],
    strategy: Optional[str],
    limit: Optional[int],
    collection_name: Optional[str],
) -> List[Dict[str, Any]]:
    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return [
            {
                "provenance": "pageindex",
                "error": "PageIndexAction not found on this agent",
            }
        ]

    results = await action.search(
        query=query,
        doc_name=doc_name,
        strategy=strategy,
        limit=limit,
        collection_name=collection_name,
    )
    return _tag_pageindex_results(results)


async def _search_web(
    resolver: Any,
    query: str,
) -> List[Dict[str, Any]]:
    action = await resolver.resolve("SerperWebSearchAction")
    if action is None:
        return [
            {
                "provenance": "web",
                "error": "SerperWebSearchAction not found on this agent",
            }
        ]

    results = await action.search(query=query)
    return _tag_web_results(results)


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> List[Dict[str, Any]]:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return [{"error": "ActionResolver not available"}]

    query = arguments["query"]
    source = arguments.get("source", "pageindex")
    doc_name = arguments.get("doc_name")
    strategy = arguments.get("strategy")
    limit = arguments.get("limit")
    collection_name = arguments.get("collection_name")

    if source == "all":
        pi_coro = _search_pageindex(
            resolver, query, doc_name, strategy, limit, collection_name
        )
        web_coro = _search_web(resolver, query)
        pi_results, web_results = await asyncio.gather(pi_coro, web_coro)
        return pi_results + web_results

    if source == "web":
        return await _search_web(resolver, query)

    return await _search_pageindex(
        resolver, query, doc_name, strategy, limit, collection_name
    )
