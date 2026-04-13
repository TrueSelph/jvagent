"""Vectorless retrieval service for PageIndex document graph.

Two-stage pipeline:
    1. Lexical candidate retrieval (BM25 over inverted index -- O(|query_terms|))
    2. Strategy-specific refinement (tree_search / direct / walker)

Falls back gracefully to full-scan retrieval when the lexical index has no
data (e.g. documents ingested before the index existed).
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)
from jvspatial.db import get_database_manager
from jvspatial.env import env

from .config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_candidate_k,
    get_pageindex_enable_lexical_index,
    get_pageindex_max_docs_for_tree_search,
    get_pageindex_max_summary_chars,
    get_pageindex_max_tree_prompt_tokens,
    get_pageindex_retrieval_excerpt_source,
)
from .document_walker import DocumentWalker
from .documents import get_document_root, get_document_roots
from .llm_bridge import get_pageindex_model_action
from .models import (
    DocumentContentEdge,
    DocumentNode,
    DocumentRootNode,
    copy_included_fields,
    node_enabled,
    node_to_result,
)

logger = logging.getLogger(__name__)


def _row_from_node(
    node: DocumentNode,
    include: Optional[List[str]],
) -> Dict[str, Any]:
    base = node_to_result(node)
    return copy_included_fields(node, base, include)


def _parse_llm_json_object(raw: str) -> Dict[str, Any]:
    """Parse the first JSON object from model output (ignore trailing prose)."""
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(raw.strip())
    if not isinstance(obj, dict):
        raise ValueError("expected JSON object from tree search")
    return obj


def _truncate_tree_excerpt(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\u2026"
    return text


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


# ---------------------------------------------------------------------------
# Lexical candidate helpers
# ---------------------------------------------------------------------------


async def _lexical_candidates(
    query: str,
    collection_name: str,
    doc_name: Optional[str] = None,
    metadata_filter: Optional[Dict[str, Any]] = None,
    candidate_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Retrieve BM25-ranked candidates from the lexical index.

    Resolves ``metadata_filter`` to a set of allowed doc_names before querying
    the index.  Returns empty list when the index has no data or the feature
    is disabled (signals caller to fall back).
    """
    if not get_pageindex_enable_lexical_index():
        return []

    from .lexical_index import search as lex_search

    allowed_doc_names: Optional[List[str]] = None
    if metadata_filter and not doc_name:
        roots = await get_document_roots(
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
        allowed_doc_names = [r.doc_name for r in roots]
        if not allowed_doc_names:
            return []

    k = candidate_k if candidate_k is not None else get_pageindex_candidate_k()

    try:
        return await lex_search(
            query=query,
            collection_name=collection_name,
            doc_name=doc_name,
            allowed_doc_names=allowed_doc_names,
            candidate_k=k,
        )
    except Exception:
        logger.debug("Lexical index search failed; falling back", exc_info=True)
        return []


def _top_doc_names(candidates: List[Dict[str, Any]], max_docs: int) -> List[str]:
    """Aggregate candidate scores by doc_name and return top-N doc names."""
    doc_scores: Dict[str, float] = {}
    for c in candidates:
        dn = c["doc_name"]
        doc_scores[dn] = doc_scores.get(dn, 0.0) + c["score"]
    ranked = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    return [dn for dn, _ in ranked[:max_docs]]


# ---------------------------------------------------------------------------
# Tree building
# ---------------------------------------------------------------------------


async def _graph_to_tree(
    root: DocumentRootNode,
    max_summary_chars: Optional[int] = None,
    excerpt_source: Optional[str] = None,
    only_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """Build PageIndex-style tree from jvspatial graph."""
    max_chars = (
        max_summary_chars
        if max_summary_chars is not None
        else get_pageindex_max_summary_chars()
    )
    mode = (
        excerpt_source
        if excerpt_source is not None
        else get_pageindex_retrieval_excerpt_source()
    )

    async def _node_to_dict(node: DocumentNode) -> Optional[Dict[str, Any]]:
        if only_enabled and not node_enabled(node):
            return None
        children = await node.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        text_body = node.text or ""
        text_stripped = text_body.strip()
        summary_fields = node.summary or node.prefix_summary or ""
        if mode == "text":
            if text_stripped:
                summary_val = _truncate_tree_excerpt(text_body, max_chars)
            else:
                summary_val = _truncate_tree_excerpt(summary_fields, max_chars)
        else:
            summary_fields_strip = (summary_fields or "").strip()
            if summary_fields_strip:
                summary_val = _truncate_tree_excerpt(summary_fields, max_chars)
            elif text_stripped:
                summary_val = _truncate_tree_excerpt(text_body, max_chars)
            else:
                summary_val = ""
        d: Dict[str, Any] = {
            "title": node.title or "",
            "node_id": str(node.node_id or ""),
            "summary": summary_val or node.title or "",
        }
        prefix_val = node.prefix_summary
        if prefix_val is not None and prefix_val != summary_val:
            d["prefix_summary"] = (
                (prefix_val[:max_chars] + "\u2026")
                if len(prefix_val) > max_chars
                else prefix_val
            )
        if children:
            child_parts = await asyncio.gather(*(_node_to_dict(c) for c in children))
            d["nodes"] = [c for c in child_parts if c is not None]
        return d

    children = await root.outgoing(node=DocumentNode, edge=DocumentContentEdge)
    if not children:
        return []
    top = await asyncio.gather(*(_node_to_dict(c) for c in children))
    return [c for c in top if c is not None]


# ---------------------------------------------------------------------------
# Strategy: tree_search
# ---------------------------------------------------------------------------


async def _search_via_tree_search(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    max_summary_chars: Optional[int] = None,
    max_tree_prompt_tokens: Optional[int] = None,
    only_enabled: bool = True,
    include: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """LLM-based tree search with lexical pre-selection of documents."""
    from .core.utils import (
        count_tokens,
        get_json_content,
        llm_acompletion,
        remove_fields,
    )

    max_tokens = (
        max_tree_prompt_tokens
        if max_tree_prompt_tokens is not None
        else get_pageindex_max_tree_prompt_tokens()
    )
    max_docs = get_pageindex_max_docs_for_tree_search()

    # --- Document selection: lexical-guided or legacy first-N ---
    candidates = await _lexical_candidates(
        query,
        collection_name,
        doc_name=doc_name,
        metadata_filter=metadata_filter,
    )

    if doc_name:
        root = await get_document_root(doc_name, collection_name=collection_name)
        roots = [root] if root else []
    elif candidates:
        top_docs = _top_doc_names(candidates, max_docs)
        root_tasks = [
            get_document_root(dn, collection_name=collection_name) for dn in top_docs
        ]
        roots = [r for r in await asyncio.gather(*root_tasks) if r]
    else:
        roots = await get_document_roots(
            collection_name=collection_name,
            metadata_filter=metadata_filter,
        )
        roots = roots[:max_docs]

    if not roots:
        return []

    model = model or env("PAGEINDEX_TREE_SEARCH_MODEL", default="gpt-4o-mini")
    api_key = env("OPENAI_API_KEY")
    if not api_key and not get_pageindex_model_action():
        logger.warning(
            "PageIndex tree search requires OPENAI_API_KEY or a model action in context; "
            "falling back to direct search"
        )
        return await _search_via_direct(
            context,
            query,
            doc_name,
            limit,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
            only_enabled=only_enabled,
            include=include,
        )

    all_results: List[Dict[str, Any]] = []

    trees = await asyncio.gather(
        *(
            _graph_to_tree(
                r,
                max_summary_chars=max_summary_chars,
                only_enabled=only_enabled,
            )
            for r in roots
        )
    )

    for root, tree in zip(roots, trees):
        doc_name_val = root.doc_name

        try:
            if not tree:
                continue
            tree_no_text = remove_fields(tree, fields=["text"])

            tree_seen: Set[str] = set()
            deduped = []
            for n in tree_no_text:
                if n["node_id"] not in tree_seen:
                    tree_seen.add(n["node_id"])
                    deduped.append(n)
            tree_no_text = deduped

            tree_str = json.dumps(tree_no_text, separators=(",", ":"))
            tree_tokens = count_tokens(tree_str, model=model or "gpt-4o-mini")
            if tree_tokens > max_tokens:
                logger.warning(
                    f"PageIndex tree for doc '{doc_name_val}' exceeds token budget "
                    f"({tree_tokens} > {max_tokens}); falling back to direct search"
                )
                direct_results = await _search_via_direct(
                    context,
                    query,
                    doc_name_val,
                    limit,
                    collection_name=collection_name,
                    metadata_filter=metadata_filter,
                    only_enabled=only_enabled,
                    include=include,
                )
                all_results.extend(direct_results[: limit - len(all_results)])
                if len(all_results) >= limit:
                    break
                continue

            prompt = f"""You are given a question and a tree structure of a document.
Each node contains a node id, node title, and a section excerpt (truncated text from the document body).
Your task is to find all nodes that are likely to contain the answer to the question.

Question: {query}

Document tree structure:
{tree_str}

Please reply in the following JSON format:
{{
    "thinking": "<Your thinking process on which nodes are relevant to the question>",
    "node_list": ["node_id_1", "node_id_2", ..., "node_id_n"]
}}
Directly return the final JSON structure. Do not output anything else.
"""

            response = await llm_acompletion(model=model, prompt=prompt)
            if not response:
                logger.warning(
                    "PageIndex tree search LLM call failed; falling back to direct"
                )
                return await _search_via_direct(
                    context,
                    query,
                    doc_name,
                    limit,
                    collection_name=collection_name,
                    metadata_filter=metadata_filter,
                    only_enabled=only_enabled,
                    include=include,
                )

            raw = get_json_content(response)
            parsed_resp = _parse_llm_json_object(raw)
            node_list = parsed_resp.get("node_list") or []
            if not isinstance(node_list, list):
                node_list = []

            unique_nids = list(dict.fromkeys(str(nid) for nid in node_list[:limit]))
            if unique_nids:
                nodes = await DocumentNode.find(
                    {
                        "context.node_id": {"$in": unique_nids},
                        "context.doc_name": doc_name_val,
                        "context.collection_name": collection_name,
                    }
                )
                nid_order = {nid: idx for idx, nid in enumerate(unique_nids)}
                nodes.sort(key=lambda n: nid_order.get(n.node_id, float("inf")))
                for node in nodes:
                    if only_enabled and not node_enabled(node):
                        continue
                    all_results.append(_row_from_node(node, include))
                    if len(all_results) >= limit:
                        break

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                f"PageIndex tree search parse error: {e}; falling back to direct"
            )
            return await _search_via_direct(
                context,
                query,
                doc_name,
                limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                only_enabled=only_enabled,
                include=include,
            )
        except Exception:
            logger.exception(
                "Unexpected error in PageIndex tree search; falling back to direct"
            )
            return await _search_via_direct(
                context,
                query,
                doc_name,
                limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                only_enabled=only_enabled,
                include=include,
            )

        if len(all_results) >= limit:
            break

    return (
        all_results[:limit]
        if all_results
        else await _search_via_direct(
            context,
            query,
            doc_name,
            limit,
            collection_name=collection_name,
            metadata_filter=metadata_filter,
            only_enabled=only_enabled,
            include=include,
        )
    )


# ---------------------------------------------------------------------------
# Strategy: direct (candidate-first when lexical index available)
# ---------------------------------------------------------------------------


async def _search_via_direct(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    only_enabled: bool = True,
    include: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search using lexical candidates then hydrate, or fall back to regex scan."""

    # --- Two-stage path: lexical candidates -> hydrate by ID ---
    candidates = await _lexical_candidates(
        query,
        collection_name,
        doc_name=doc_name,
        metadata_filter=metadata_filter,
    )
    if candidates:
        mult = 12 if only_enabled else 3
        candidate_ids = [c["node_id"] for c in candidates[: limit * mult]]
        out: List[Dict[str, Any]] = []
        for nid in candidate_ids:
            try:
                data = await context.database.get("node", nid)
                if not data:
                    continue
                node = await context._deserialize_entity(DocumentNode, data)
                if not node:
                    continue
                if node.collection_name != collection_name:
                    continue
                if doc_name and node.doc_name != doc_name:
                    continue
                if only_enabled and not node_enabled(node):
                    continue
                out.append(_row_from_node(node, include))
                if len(out) >= limit:
                    break
            except Exception as e:
                logger.debug(f"Skipping candidate node {nid}: {e}")
        if out:
            return out

    # --- Fallback: original full-scan regex search ---
    return await _search_via_direct_scan(
        context,
        query,
        doc_name,
        limit,
        collection_name=collection_name,
        metadata_filter=metadata_filter,
        only_enabled=only_enabled,
        include=include,
    )


async def _search_via_direct_scan(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    only_enabled: bool = True,
    include: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Original full-scan regex search (fallback when lexical index is empty)."""
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
    cap = limit * 25 if only_enabled else limit
    for data in results[:cap]:
        try:
            node = await context._deserialize_entity(DocumentNode, data)
            if not node:
                continue
            if only_enabled and not node_enabled(node):
                continue
            out.append(_row_from_node(node, include))
            if len(out) >= limit:
                break
        except Exception as e:
            logger.debug(f"Skipping invalid node: {e}")
    return out


# ---------------------------------------------------------------------------
# Strategy: walker (lexical-guided root selection)
# ---------------------------------------------------------------------------


async def _search_via_walker(
    context: GraphContext,
    query: str,
    doc_name: Optional[str],
    limit: int,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    only_enabled: bool = True,
    include: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search using DocumentWalker traversal from document roots.

    When the lexical index has data, walks only the top-scoring document roots
    instead of all roots in the collection.
    """
    if doc_name:
        root = await get_document_root(doc_name, collection_name=collection_name)
        roots = [root] if root else []
    else:
        candidates = await _lexical_candidates(
            query,
            collection_name,
            metadata_filter=metadata_filter,
        )
        if candidates:
            max_docs = get_pageindex_max_docs_for_tree_search()
            top_docs = _top_doc_names(candidates, max_docs)
            root_tasks = [
                get_document_root(dn, collection_name=collection_name)
                for dn in top_docs
            ]
            roots = [r for r in await asyncio.gather(*root_tasks) if r]
        else:
            roots = await get_document_roots(
                collection_name=collection_name,
                metadata_filter=metadata_filter,
            )
    if not roots:
        return []

    all_results: List[Dict[str, Any]] = []
    for root in roots:
        walker = DocumentWalker(
            query=query,
            limit=limit - len(all_results),
            only_enabled=only_enabled,
            include=include,
        )
        await walker.spawn(root)
        report = await walker.get_report()
        for item in report:
            if isinstance(item, dict):
                all_results.append(item)
        if len(all_results) >= limit:
            break

    return all_results[:limit]


# ---------------------------------------------------------------------------
# URL enrichment (for citations when include_references is True)
# ---------------------------------------------------------------------------


async def _resolve_doc_urls(
    results: List[Dict[str, Any]],
    collection_name: str,
) -> None:
    """Batch-resolve document URLs and enrich result dicts in-place."""
    doc_names = {r["doc_name"] for r in results if r.get("doc_name")}
    if not doc_names:
        return

    names_list = list(doc_names)
    roots = await asyncio.gather(
        *(
            get_document_root(name, collection_name=collection_name)
            for name in names_list
        )
    )
    url_map: Dict[str, Optional[str]] = {}
    for name, root in zip(names_list, roots):
        if root:
            url = getattr(root, "doc_url", None)
            if not url and root.metadata:
                url = root.metadata.get("doc_url") or root.metadata.get("url")
            url_map[name] = url
        else:
            url_map[name] = None

    for r in results:
        r["doc_url"] = url_map.get(r.get("doc_name", ""))


# ---------------------------------------------------------------------------
# Public entry point (unchanged signature)
# ---------------------------------------------------------------------------


async def search_documents(
    query: str,
    doc_name: Optional[str] = None,
    strategy: str = "tree_search",
    limit: int = 20,
    model: Optional[str] = None,
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
    max_summary_chars: Optional[int] = None,
    max_tree_prompt_tokens: Optional[int] = None,
    include_references: bool = True,
    only_enabled: bool = True,
    include: Optional[List[str]] = None,
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
        max_summary_chars: Max chars per node summary in tree prompt (default from config: 300)
        max_tree_prompt_tokens: Max tokens for tree in tree-search prompt (default from config: 16000)
        include_references: When True, resolve and attach doc_url per result; when False, omit doc_url
        only_enabled: When True, omit chunks with enabled=false from all strategies
        include: Optional extra node metadata keys per hit (e.g. hierarchy, content_type, pageindex_node_id)

    Returns:
        List of dicts with title, text, summary, doc_name, node_id, structure, content,
        start_index, end_index, physical_index, and doc_url when include_references is True
    """
    try:
        manager = get_database_manager()
        db = manager.get_database(PAGEINDEX_DB_NAME)
    except (ValueError, KeyError):
        logger.warning(f"PageIndex database '{PAGEINDEX_DB_NAME}' not registered")
        return []

    context = GraphContext(database=db)
    try:
        prev = get_default_context()
    except RuntimeError:
        prev = None

    try:
        set_default_context(context)

        if strategy == "tree_search":
            results = await _search_via_tree_search(
                context,
                query,
                doc_name,
                limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                model=model,
                max_summary_chars=max_summary_chars,
                max_tree_prompt_tokens=max_tree_prompt_tokens,
                only_enabled=only_enabled,
                include=include,
            )
        elif strategy == "walker":
            results = await _search_via_walker(
                context,
                query,
                doc_name,
                limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                only_enabled=only_enabled,
                include=include,
            )
        else:
            results = await _search_via_direct(
                context,
                query,
                doc_name,
                limit,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                only_enabled=only_enabled,
                include=include,
            )

        if include_references:
            await _resolve_doc_urls(results, collection_name)
        else:
            for r in results:
                r.pop("doc_url", None)
        return results
    finally:
        if prev is not None:
            set_default_context(prev)
