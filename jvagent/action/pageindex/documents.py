"""PageIndex document operations.

Wraps vendored PageIndex core (page_index, md_to_tree) for document assimilation,
persisting the resulting structure to the jvspatial graph database.
"""

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)
from jvspatial.db import get_database_manager

from .adapter import tree_to_graph
from .config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_doc_description,
    get_pageindex_max_token_num_each_node,
    get_pageindex_node_summary,
    get_pageindex_node_text,
    get_pageindex_summary_token_threshold,
    initialize_pageindex_database,
)
from .core import md_to_tree, page_index
from .llm_bridge import set_pageindex_model_action
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode

logger = logging.getLogger(__name__)

# Max chunks returned in one list response (when per_page is 0 = "all", or cap page size).
CHUNK_LIST_MAX = 5000


async def _get_app_id_from_node() -> Optional[str]:
    """Get app_id from App node. JVAGENT_APP_ID env overrides when set in config."""
    from jvagent.core.app import App

    app = await App.get()
    return getattr(app, "app_id", None) if app else None


def _safe_get_prev_context() -> Optional[GraphContext]:
    """Return the current default context, or None if none is set."""
    try:
        return get_default_context()
    except RuntimeError:
        return None


def _safe_restore_context(prev: Optional[GraphContext]) -> None:
    """Restore a previous default context if one was captured."""
    if prev is not None:
        set_default_context(prev)


def _to_yes_no(value: Any, default: bool) -> str:
    """Normalize bool-like value to yes/no. None -> default; yes/true/1 -> yes; else no."""
    if value is None:
        return "yes" if default else "no"
    v = str(value).lower().strip()
    return "yes" if v in ("yes", "true", "1") else "no"


def _build_metadata_query(metadata_filter: Dict[str, Any]) -> Dict[str, Any]:
    """Build query dict for metadata filter.

    Supports single-key, multi-key, and list-valued filters (OR semantics for lists).
    Uses dot notation for all keys to allow matching a subset of metadata.
    """
    if not metadata_filter:
        return {}

    query = {}
    for k, v in metadata_filter.items():
        if isinstance(v, list):
            query[f"context.metadata.{k}"] = {"$in": v}
        else:
            query[f"context.metadata.{k}"] = v
    return query


def _get_pageindex_context() -> GraphContext:
    """Get GraphContext for the PageIndex database."""
    manager = get_database_manager()
    db = manager.get_database(PAGEINDEX_DB_NAME)
    return GraphContext(database=db)


async def assimilate_document(
    doc: Union[str, Path, bytes],
    *,
    doc_name: Optional[str] = None,
    model: Optional[str] = "gpt-4o-mini",
    model_action: Optional[Any] = None,
    if_add_node_id: str = "yes",
    if_add_node_text: str = "yes",
    if_add_node_summary: Optional[str] = None,
    if_add_doc_description: str = "no",
    toc_check_page_num: Optional[int] = None,
    max_page_num_each_node: Optional[int] = None,
    max_token_num_each_node: Optional[int] = None,
    summary_token_threshold: Optional[int] = None,
    persist: bool = True,
    collection_name: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    doc_description: Optional[str] = None,
    doc_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Assimilate a PDF or Markdown document via PageIndex and optionally persist to graph.

    Args:
        doc: File path (str/Path), or bytes (PDF), or BytesIO
        doc_name: Override document name (default: derived from file)
        model: LLM model for tree generation
        model_action: Optional LanguageModelAction for observability (when in agent context)
        if_add_node_id: Add node_id to structure
        if_add_node_text: Add text to nodes
        if_add_node_summary: Add summaries (None = use action config via get_pageindex_node_summary)
        if_add_doc_description: Add doc description
        toc_check_page_num: Pages to check for TOC (PDF)
        max_page_num_each_node: Max pages per node (PDF)
        max_token_num_each_node: Max tokens per node (PDF)
        summary_token_threshold: Token threshold for node summaries (default 200)
        persist: Whether to persist to graph database
        collection_name: Collection this document belongs to (default: "default")
        metadata: Custom key-value metadata for filtering at query time
        doc_description: Optional user-provided document description (overrides LLM-generated if set)
        doc_url: Source URL of the document resource (stored on DocumentRootNode for reference citations)

    Returns:
        Dict with doc_name, structure, doc_description (if requested), _root_id (if persist)
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())

    # Core expects a valid model string (tiktoken.encoding_for_model fails on None)
    model = model or "gpt-4o-mini"

    # Normalize: true/yes/1 -> "yes", false/no/0 -> "no" for core; use config when None
    if_add_node_summary = _to_yes_no(if_add_node_summary, get_pageindex_node_summary())
    if_add_node_text = _to_yes_no(if_add_node_text, get_pageindex_node_text())
    if_add_doc_description = _to_yes_no(
        if_add_doc_description, get_pageindex_doc_description()
    )
    if max_token_num_each_node is None:
        max_token_num_each_node = get_pageindex_max_token_num_each_node()
    if summary_token_threshold is None:
        summary_token_threshold = get_pageindex_summary_token_threshold() or 200

    if model_action:
        set_pageindex_model_action(model_action)
    try:
        is_pdf = False
        if isinstance(doc, bytes):
            doc = BytesIO(doc)
            is_pdf = True
        elif isinstance(doc, BytesIO):
            is_pdf = True
        elif isinstance(doc, (str, Path)):
            path = Path(doc)
            ext = path.suffix.lower()
            is_pdf = ext == ".pdf"

        if is_pdf:
            # page_index() uses asyncio.run() internally; run in executor to avoid
            # "asyncio.run() cannot be called from a running event loop"
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: page_index(
                    doc,
                    model=model,
                    toc_check_page_num=toc_check_page_num,
                    max_page_num_each_node=max_page_num_each_node,
                    max_token_num_each_node=max_token_num_each_node,
                    if_add_node_id=if_add_node_id,
                    if_add_node_text=if_add_node_text,
                    if_add_node_summary=if_add_node_summary,
                    if_add_doc_description=if_add_doc_description,
                ),
            )
        else:
            result = await md_to_tree(
                str(doc),
                if_add_node_id=if_add_node_id,
                if_add_node_text=if_add_node_text,
                if_add_node_summary=if_add_node_summary,
                if_add_doc_description=if_add_doc_description,
                model=model,
                summary_token_threshold=summary_token_threshold or 200,
            )

        name = result.get("doc_name", "")
        if doc_name:
            result["doc_name"] = doc_name
            name = doc_name

        if persist and result.get("structure"):
            result["collection_name"] = collection_name
            result["metadata"] = metadata
            if doc_description is not None:
                result["doc_description"] = doc_description
            if doc_url is not None:
                result["doc_url"] = doc_url
            root_id = await tree_to_graph(result)
            result["_root_id"] = root_id
            logger.info(f"Assimilated document '{name}' (root={root_id})")

        return result
    finally:
        if model_action:
            set_pageindex_model_action(None)


async def get_document_roots(
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[DocumentRootNode]:
    """Get DocumentRootNodes filtered by collection and optional metadata."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        query: Dict[str, Any] = {"context.collection_name": collection_name}
        query.update(_build_metadata_query(metadata_filter or {}))
        return await DocumentRootNode.find(query)
    finally:
        _safe_restore_context(prev)


async def get_document_root(
    doc_name: str,
    collection_name: str = "default",
) -> Optional[DocumentRootNode]:
    """Get DocumentRootNode by doc_name and collection_name."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        query: Dict[str, Any] = {
            "context.doc_name": doc_name,
            "context.collection_name": collection_name,
        }
        roots = await DocumentRootNode.find(query)
        return roots[0] if roots else None
    finally:
        _safe_restore_context(prev)


async def list_documents(
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """List documents in the PageIndex graph, optionally filtered by collection and metadata."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        query: Dict[str, Any] = {"context.collection_name": collection_name}
        query.update(_build_metadata_query(metadata_filter or {}))
        roots = await DocumentRootNode.find(query)
        return [
            {
                "doc_name": r.doc_name,
                "doc_description": r.doc_description,
                "doc_url": r.doc_url,
                "root_id": r.id,
                "collection_name": r.collection_name,
                "metadata": r.metadata,
            }
            for r in roots
        ]
    finally:
        _safe_restore_context(prev)


async def delete_document(
    doc_name: str,
    collection_name: str = "default",
) -> bool:
    """Delete a document and all its nodes from the PageIndex graph."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return False

    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)

        # Clean up lexical index before cascade-deleting graph nodes
        try:
            from .lexical_index import remove_document_nodes
            from .models import DocumentContentEdge, DocumentNode

            nodes = await DocumentNode.find(
                {
                    "context.doc_name": doc_name,
                    "context.collection_name": collection_name,
                }
            )
            if nodes:
                await remove_document_nodes([n.id for n in nodes], collection_name)
        except Exception:
            logger.debug(
                "Lexical index cleanup failed for document deletion",
                exc_info=True,
            )

        await root.delete()
        logger.info(f"Deleted document '{doc_name}'")
        return True
    finally:
        _safe_restore_context(prev)


async def export_documents(
    collection_name: str = "default",
    doc_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Export documents and their graph structure."""
    from .models import DocumentContentEdge, DocumentNode

    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    logger.debug(f"Exporting documents in collection: {collection_name}")
    try:
        set_default_context(context)
        query: Dict[str, Any] = {"context.collection_name": collection_name}
        if doc_name:
            query["context.doc_name"] = doc_name
        roots = await DocumentRootNode.find(query)
        nodes = await DocumentNode.find(query)

        node_ids = {r.id for r in roots} | {n.id for n in nodes}
        all_edges = await DocumentContentEdge.find({})
        edges = [
            e
            for e in all_edges
            if getattr(e, "source", None) in node_ids
            or getattr(e, "target", None) in node_ids
        ]

        return {
            "roots": [r.model_dump() for r in roots],
            "nodes": [n.model_dump() for n in nodes],
            "edges": [e.model_dump() for e in edges],
        }
    finally:
        _safe_restore_context(prev)


async def import_documents(
    data: Dict[str, Any],
    purge: bool = False,
    collection_name: Optional[str] = None,
) -> None:
    """Import documents and their graph structure."""
    from .models import DocumentContentEdge, DocumentNode

    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        if purge and collection_name:
            try:
                from .lexical_index import remove_collection

                await remove_collection(collection_name)
            except Exception:
                logger.debug(
                    "Lexical index cleanup failed during import purge",
                    exc_info=True,
                )
            query = {"context.collection_name": collection_name}
            roots = await DocumentRootNode.find(query)
            for root in roots:
                await root.delete()
        for root_data in data.get("roots", []):
            await DocumentRootNode(**root_data).save()
        imported_nodes: list = []
        for node_data in data.get("nodes", []):
            node = DocumentNode(**node_data)
            await node.save()
            imported_nodes.append(node)
        for edge_data in data.get("edges", []):
            await DocumentContentEdge(**edge_data).save()

        # Build lexical index for imported nodes
        if imported_nodes:
            try:
                from .lexical_index import index_node as _lex_index

                for node in imported_nodes:
                    coll = getattr(
                        node, "collection_name", collection_name or "default"
                    )
                    await _lex_index(
                        node_id=node.id,
                        doc_name=node.doc_name,
                        collection_name=coll,
                        title=node.title or "",
                        text=node.text or "",
                        summary=node.summary or "",
                        prefix_summary=node.prefix_summary or "",
                    )
            except Exception:
                logger.debug("Lexical indexing failed during import", exc_info=True)
    finally:
        _safe_restore_context(prev)


def _document_node_to_chunk_dict(node: DocumentNode) -> Dict[str, Any]:
    """Serialize a DocumentNode for chunk list/detail API responses."""
    return {
        "id": node.id,
        "title": node.title or "",
        "text": node.text or "",
        "summary": node.summary,
        "prefix_summary": node.prefix_summary,
        "structure": node.structure or "",
        "node_id": node.node_id or "",
        "start_index": node.start_index,
        "end_index": node.end_index,
        "physical_index": node.physical_index,
        "line_num": node.line_num,
        "doc_name": node.doc_name or "",
    }


def _chunk_matches_filter(query: Optional[str], node: DocumentNode) -> bool:
    if not query or not str(query).strip():
        return True
    needle = str(query).strip().lower()
    parts = [
        node.title or "",
        node.text or "",
        node.summary or "",
        node.prefix_summary or "",
        node.structure or "",
    ]
    return any(needle in p.lower() for p in parts)


def _chunk_sort_key(node: DocumentNode) -> tuple:
    return (node.structure or "", node.id or "")


def _chunk_sort_key_collection(node: DocumentNode) -> tuple:
    return (node.doc_name or "", node.structure or "", node.id or "")


def _paginate_filtered_nodes(
    filtered: List[DocumentNode],
    *,
    page: int,
    per_page: int,
) -> Tuple[List[DocumentNode], int]:
    """Slice filtered nodes for the current page; per_page <= 0 means all (capped)."""
    total = len(filtered)
    if page < 1:
        page = 1
    if per_page <= 0:
        cap = min(total, CHUNK_LIST_MAX)
        page_chunks = filtered[:cap]
    else:
        per_page = min(per_page, CHUNK_LIST_MAX)
        start = (page - 1) * per_page
        page_chunks = filtered[start : start + per_page]
    return page_chunks, total


async def _collect_subtree_node_ids(root: DocumentNode) -> List[str]:
    """All DocumentNode ids in the subtree under root (including root), via outgoing edges."""
    ordered: List[str] = []
    seen: set[str] = set()
    queue: List[DocumentNode] = [root]
    while queue:
        current = queue.pop(0)
        if current.id in seen:
            continue
        seen.add(current.id)
        ordered.append(current.id)
        children = await current.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        queue.extend(children)
    return ordered


_CHUNK_UPDATE_FIELDS = frozenset(
    {
        "title",
        "text",
        "summary",
        "prefix_summary",
        "structure",
        "node_id",
        "start_index",
        "end_index",
        "physical_index",
        "line_num",
    }
)


async def list_document_chunks(
    doc_name: str,
    collection_name: str,
    *,
    page: int = 1,
    per_page: int = 0,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    """List DocumentNode chunks for a document with optional text filter and pagination.

    per_page <= 0 means return up to CHUNK_LIST_MAX chunks (all by default, capped).
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return {"chunks": [], "total": 0}

    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        nodes = await DocumentNode.find(
            {
                "context.doc_name": doc_name,
                "context.collection_name": collection_name,
            }
        )
    finally:
        _safe_restore_context(prev)

    filtered = [n for n in nodes if _chunk_matches_filter(q, n)]
    filtered.sort(key=_chunk_sort_key)
    page_chunks, total = _paginate_filtered_nodes(filtered, page=page, per_page=per_page)

    return {
        "chunks": [_document_node_to_chunk_dict(n) for n in page_chunks],
        "total": total,
    }


async def list_collection_chunks(
    collection_name: str,
    *,
    page: int = 1,
    per_page: int = 0,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    """List all DocumentNode chunks in a collection (all documents)."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        nodes = await DocumentNode.find({"context.collection_name": collection_name})
    finally:
        _safe_restore_context(prev)

    filtered = [n for n in nodes if _chunk_matches_filter(q, n)]
    filtered.sort(key=_chunk_sort_key_collection)
    page_chunks, total = _paginate_filtered_nodes(filtered, page=page, per_page=per_page)

    return {
        "chunks": [_document_node_to_chunk_dict(n) for n in page_chunks],
        "total": total,
    }


async def update_document_metadata(
    doc_name: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Set DocumentRootNode.metadata (None clears)."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return None

    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        root.metadata = metadata
        await root.save()
        return {
            "doc_name": root.doc_name,
            "root_id": root.id,
            "metadata": root.metadata,
        }
    finally:
        _safe_restore_context(prev)


async def get_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
) -> Optional[Dict[str, Any]]:
    """Return chunk dict if the node exists and belongs to doc_name/collection."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        node = await DocumentNode.get(chunk_id)
    finally:
        _safe_restore_context(prev)

    if not node:
        return None
    if node.doc_name != doc_name or node.collection_name != collection_name:
        return None
    return _document_node_to_chunk_dict(node)


async def update_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply whitelisted field updates; refresh lexical index for this node."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        node = await DocumentNode.get(chunk_id)
        if not node or node.doc_name != doc_name or node.collection_name != collection_name:
            return None

        for key, value in updates.items():
            if key not in _CHUNK_UPDATE_FIELDS:
                continue
            if hasattr(node, key):
                setattr(node, key, value)

        await node.save()

        try:
            from .lexical_index import index_node, remove_node

            await remove_node(node.id, collection_name)
            await index_node(
                node_id=node.id,
                doc_name=node.doc_name,
                collection_name=collection_name,
                title=node.title or "",
                text=node.text or "",
                summary=node.summary or "",
                prefix_summary=node.prefix_summary or "",
            )
        except Exception:
            logger.debug(
                "Lexical index refresh failed after chunk update",
                exc_info=True,
            )

        return _document_node_to_chunk_dict(node)
    finally:
        _safe_restore_context(prev)


async def delete_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
    *,
    cascade: bool = True,
) -> bool:
    """Delete a chunk node; optionally cascade to descendants. Cleans lexical index first."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    prev = _safe_get_prev_context()
    try:
        set_default_context(context)
        node = await DocumentNode.get(chunk_id)
        if not node or node.doc_name != doc_name or node.collection_name != collection_name:
            return False

        try:
            from .lexical_index import remove_document_nodes

            if cascade:
                ids = await _collect_subtree_node_ids(node)
            else:
                ids = [node.id]
            await remove_document_nodes(ids, collection_name)
        except Exception:
            logger.debug(
                "Lexical index cleanup failed before chunk delete",
                exc_info=True,
            )

        await node.delete(cascade=cascade)
        logger.info(
            "Deleted PageIndex chunk %s (doc=%s, cascade=%s)",
            chunk_id,
            doc_name,
            cascade,
        )
        return True
    finally:
        _safe_restore_context(prev)
