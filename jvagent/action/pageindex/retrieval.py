"""Vectorless retrieval service for PageIndex document graph.

Search via database.find() with text filters, DocumentWalker traversal,
or LLM-based tree search (PageIndex recommended approach).
No vector store, no embeddings.
"""

import asyncio
import json
import os
import re
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.context import GraphContext, get_default_context, set_default_context
from jvspatial.db import get_database_manager

from .config import PAGEINDEX_DB_NAME
from .document_walker import DocumentWalker
from .llm_bridge import get_pageindex_model_action
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode
from .documents import get_document_root, get_document_roots

logger = logging.getLogger(__name__)

_MAX_DOCS_FOR_TREE_SEARCH = 5


def _build_text_query(query: str) -> Dict[str, Any]:
    """Build MongoDB-style query for direct (regex) search across title, text, summary."""
    if not query or not query.strip():
        return {"entity": "DocumentNode"}

    q = re.escape(query.strip())
    return {
        "entity": "DocumentNode",
        "$or": [
            {"context.title": {"$regex": q, "$options": "i"}},
            {"context.text": {"$regex": q, "$options": "i"}},
            {"context.summary": {"$regex": q, "$options": "i"}},
            {"context.prefix_summary": {"$regex": q, "$options": "i"}},
        ],
    }


async def _graph_to_tree(root: DocumentRootNode) -> List[Dict[str, Any]]:
    """Build PageIndex-style tree from jvspatial graph.

    Traverses DocumentRootNode -> DocumentNode hierarchy. Returns list of top-level
    nodes, each with title, node_id, summary, prefix_summary, nodes (no text).
    """

    async def _node_to_dict(node: DocumentNode) -> Dict[str, Any]:
        children = await node.outgoing(
            node=DocumentNode, edge=DocumentContentEdge
        )
        summary_val = node.summary or node.prefix_summary
        if summary_val is None and node.text:
            summary_val = (
                (node.text[:300] + "\u2026")
                if len(node.text) > 300
                else node.text
            )
        d: Dict[str, Any] = {
            "title": node.title or "",
            "node_id": str(node.node_id or ""),
            "summary": summary_val,
        }
        if node.prefix_summary is not None and node.prefix_summary != summary_val:
            d["prefix_summary"] = node.prefix_summary
        if children:
            d["nodes"] = [_node_to_dict(c) for c in children]
            d["nodes"] = await _gather(d["nodes"])
        return d

    async def _gather(coros):
        return await asyncio.gather(*coros)

    children = await root.outgoing(
        node=DocumentNode, edge=DocumentContentEdge
    )
    if not children:
        return []
    return await _gather([_node_to_dict(c) for c in children])


async def _search_via_tree_search(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search using LLM-based tree search (PageIndex recommended approach).

    Builds tree from graph, sends to LLM with query, parses node_list,
    fetches full content for selected nodes.
    """
    from .core.utils import ChatGPT_API_async, get_json_content, remove_fields

    if doc_name:
        root = await get_document_root(doc_name, collection_name=collection_name)
        roots = [root] if root else []
    else:
        roots = await get_document_roots(
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
        roots = roots[:_MAX_DOCS_FOR_TREE_SEARCH]

    if not roots:
        return []

    model = model or os.getenv("PAGEINDEX_TREE_SEARCH_MODEL", "gpt-4o-mini")
    api_key = os.getenv("CHATGPT_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key and not get_pageindex_model_action():
        logger.warning(
            "PageIndex tree search requires CHATGPT_API_KEY or OPENAI_API_KEY "
            "(or model_action in context); falling back to direct search"
        )
        return await _search_via_direct(
            context, query, doc_name, limit,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )

    all_results: List[Dict[str, Any]] = []

    for root in roots:
        if doc_name and root.doc_name != doc_name:
            continue
        doc_name_val = root.doc_name

        try:
            tree = await _graph_to_tree(root)
            if not tree:
                continue
            tree_no_text = remove_fields(tree, fields=["text"])
            
            seen = set()
            deduped = []
            for n in tree_no_text:
                if n["node_id"] not in seen:
                    seen.add(n["node_id"])
                    deduped.append(n)
            tree_no_text = deduped

            prompt = f"""You are given a question and a tree structure of a document.
Each node contains a node id, node title, and a corresponding summary.
Your task is to find all nodes that are likely to contain the answer to the question.

Question: {query}

Document tree structure:
{json.dumps(tree_no_text, indent=2)}

Please reply in the following JSON format:
{{
    "thinking": "<Your thinking process on which nodes are relevant to the question>",
    "node_list": ["node_id_1", "node_id_2", ..., "node_id_n"]
}}
Directly return the final JSON structure. Do not output anything else.
"""

            response = await ChatGPT_API_async(model, prompt, api_key=api_key)
            if not response or response == "Error":
                logger.warning("PageIndex tree search LLM call failed; falling back to direct")
                return await _search_via_direct(
                    context, query, doc_name, limit,
                    collection_name=collection_name,
                    metadata_filter=metadata_filter,
                )

            raw = get_json_content(response)
            parsed = json.loads(raw)
            node_list = parsed.get("node_list") or []
            if not isinstance(node_list, list):
                node_list = []

            seen: set = set()
            for nid in node_list[:limit]:
                nid_str = str(nid)
                key = (nid_str, doc_name_val)
                if key in seen:
                    continue
                nodes = await DocumentNode.find({
                    "context.node_id": nid_str,
                    "context.doc_name": doc_name_val,
                    "context.collection_name": collection_name,
                })
                for node in nodes:
                    seen.add(key)
                    content = node.summary or node.text or node.title or ""
                    all_results.append({
                        "node_id": node.id,
                        "title": node.title,
                        "text": node.text,
                        "summary": node.summary,
                        "doc_name": node.doc_name,
                        "structure": node.structure,
                        "content": content[:2000] if content else "",
                    })
                    if len(all_results) >= limit:
                        break
                if len(all_results) >= limit:
                    break

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                f"PageIndex tree search parse error: {e}; falling back to direct"
            )
            return await _search_via_direct(
                context, query, doc_name, limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
            )
        except Exception as e:
            logger.warning(
                f"PageIndex tree search error: {e}; falling back to direct",
                exc_info=True,
            )
            return await _search_via_direct(
                context, query, doc_name, limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
            )

        if len(all_results) >= limit:
            break

    return all_results[:limit] if all_results else await _search_via_direct(
        context, query, doc_name, limit,
        collection_name=collection_name,
        metadata_filter=metadata_filter,
    )


async def search_documents(
    query: str,
    doc_name: Optional[str] = None,
    strategy: str = "tree_search",
    limit: int = 20,
    model: Optional[str] = None,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search documents using vectorless retrieval.

    Args:
        query: Search query (text/substring)
        doc_name: Optional document name to scope search
        strategy: "tree_search" (LLM reasoning, recommended), "direct" (database.find),
            or "walker" (DocumentWalker traversal)
        limit: Max results to return
        model: LLM model for tree_search (default: PAGEINDEX_TREE_SEARCH_MODEL or gpt-4o-mini)
        collection_name: Collection to search (default: "default")
        metadata_filter: Optional key-value filter to narrow results by document metadata

    Returns:
        List of dicts with title, text, summary, doc_name, node_id, structure, content
    """
    try:
        manager = get_database_manager()
        db = manager.get_database(PAGEINDEX_DB_NAME)
    except (ValueError, KeyError):
        logger.warning(
            f"PageIndex database '{PAGEINDEX_DB_NAME}' not registered"
        )
        return []

    context = GraphContext(database=db)
    prev = get_default_context()

    try:
        set_default_context(context)

        if strategy == "tree_search":
            return await _search_via_tree_search(
                context, query, doc_name, limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                model=model,
            )
        if strategy == "walker":
            return await _search_via_walker(
                context, query, doc_name, limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
            )
        return await _search_via_direct(
            context, query, doc_name, limit,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
    finally:
        set_default_context(prev)


async def _search_via_direct(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search using database.find with text filters."""
    db_query = _build_text_query(query)
    db_query["context.collection_name"] = collection_name

    if metadata_filter:
        roots = await get_document_roots(
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
        doc_names = [r.doc_name for r in roots]
        if not doc_names:
            return []
        if doc_name:
            if doc_name not in doc_names:
                return []
            db_query["context.doc_name"] = doc_name
        else:
            db_query["context.doc_name"] = {"$in": doc_names}
    elif doc_name:
        db_query["context.doc_name"] = doc_name

    results = await context.database.find("node", db_query)
    out: List[Dict[str, Any]] = []
    for data in results[:limit]:
        try:
            node = await context._deserialize_entity(DocumentNode, data)
            if not node:
                continue
            content = node.summary or node.text or node.title or ""
            out.append(
                {
                    "node_id": node.id,
                    "title": node.title,
                    "text": node.text,
                    "summary": node.summary,
                    "doc_name": node.doc_name,
                    "structure": node.structure,
                    "content": content[:2000] if content else "",
                }
            )
        except Exception as e:
            logger.debug(f"Skipping invalid node: {e}")
    return out


async def _search_via_walker(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search using DocumentWalker traversal from document roots."""
    if doc_name:
        root = await get_document_root(doc_name, collection_name=collection_name)
        roots = [root] if root else []
    else:
        roots = await get_document_roots(
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
    if not roots:
        return []

    all_results: List[Dict[str, Any]] = []
    for root in roots:
        if doc_name and root.doc_name != doc_name:
            continue
        walker = DocumentWalker(query=query)
        await walker.spawn(root)
        report = await walker.get_report()
        for item in report:
            if isinstance(item, dict):
                all_results.append(item)
        if len(all_results) >= limit:
            break

    return all_results[:limit]
