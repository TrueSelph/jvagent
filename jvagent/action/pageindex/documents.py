"""PageIndex document operations.

Wraps vendored PageIndex core (page_index, md_to_tree) for document assimilation,
persisting the resulting structure to the jvspatial graph database.
"""

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
from .models import DocumentRootNode

logger = logging.getLogger(__name__)


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

    For single-key filters, uses context.metadata.k = v.
    For multi-key filters, matches the full metadata dict to ensure AND semantics
    work correctly with JSON backend (context.metadata must contain all keys).
    """
    if not metadata_filter:
        return {}
    if len(metadata_filter) == 1:
        k, v = next(iter(metadata_filter.items()))
        return {f"context.metadata.{k}": v}
    # Multi-key: use $eq so QueryEngine matches dict equality (not operator dict)
    return {"context.metadata": {"$eq": metadata_filter}}


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
    initialize_pageindex_database()

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
    initialize_pageindex_database()
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
    initialize_pageindex_database()
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
    initialize_pageindex_database()
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
    initialize_pageindex_database()
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

    initialize_pageindex_database()
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

    initialize_pageindex_database()
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
