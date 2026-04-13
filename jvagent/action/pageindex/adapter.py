"""Tree-to-graph adapter for PageIndex output.

Transforms PageIndex tree structure into jvspatial Node/Edge graph
and persists to the PageIndex database.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)
from jvspatial.db import get_database_manager

from .config import PAGEINDEX_DB_NAME
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode

logger = logging.getLogger(__name__)


def _count_structure_nodes(structure: Any) -> int:
    if isinstance(structure, dict):
        return 1 + sum(
            _count_structure_nodes(c) for c in (structure.get("nodes") or [])
        )
    if isinstance(structure, list):
        return sum(_count_structure_nodes(item) for item in structure)
    return 0


def _extract_node_data(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract DocumentNode fields from a PageIndex structure item."""
    hier = item.get("hierarchy")
    if hier is not None and not isinstance(hier, list):
        hier = list(hier) if isinstance(hier, (tuple,)) else None
    enabled = item.get("enabled", True)
    if enabled is not None and not isinstance(enabled, bool):
        enabled = bool(enabled)
    if enabled is None:
        enabled = True
    return {
        "title": item.get("title", ""),
        "node_id": str(item.get("node_id", "")),
        "text": item.get("text", "") or "",
        "summary": item.get("summary"),
        "prefix_summary": item.get("prefix_summary"),
        "physical_index": item.get("physical_index"),
        "start_index": item.get("start_index"),
        "end_index": item.get("end_index"),
        "structure": str(item.get("structure", "")),
        "line_num": item.get("line_num"),
        "enabled": enabled,
        "content_type": item.get("content_type"),
        "hierarchy": hier,
    }


async def _tree_to_nodes(
    structure: Any,
    doc_name: str,
    collection_name: str,
    context: GraphContext,
    parent_node: Optional[DocumentNode],
) -> List[DocumentNode]:
    """Recursively convert tree structure to DocumentNodes and edges.

    Args:
        structure: Tree node (dict) or list of tree nodes
        doc_name: Document identifier
        collection_name: Collection this document belongs to
        context: GraphContext for persistence
        parent_node: Parent DocumentNode (or None for root-level)

    Returns:
        List of created DocumentNode instances
    """
    top_level: List[DocumentNode] = []

    async def process_item(
        item: Dict[str, Any], parent: Optional[DocumentNode]
    ) -> DocumentNode:
        data = _extract_node_data(item)
        data["doc_name"] = doc_name
        data["collection_name"] = collection_name

        node = DocumentNode(**data)
        await node.set_context(context)
        await node.save()

        try:
            from .lexical_index import index_node as _lex_index_node

            await _lex_index_node(
                node_id=node.id,
                doc_name=doc_name,
                collection_name=collection_name,
                title=data.get("title", ""),
                text=data.get("text", ""),
                summary=data.get("summary") or "",
                prefix_summary=data.get("prefix_summary") or "",
            )
        except Exception:
            logger.debug(
                f"Lexical index: failed to index node {node.id}", exc_info=True
            )

        if parent:
            await parent.connect(node, edge=DocumentContentEdge, direction="out")

        children = item.get("nodes") or []
        for child_item in children:
            await process_item(child_item, node)

        return node

    if isinstance(structure, list):
        for item in structure:
            if isinstance(item, dict):
                node = await process_item(item, parent_node)
                top_level.append(node)
    elif isinstance(structure, dict):
        node = await process_item(structure, parent_node)
        top_level.append(node)

    return top_level


async def persist_structure(
    doc_name: str,
    structure: Any,
    doc_description: Optional[str] = None,
    collection_name: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    doc_url: Optional[str] = None,
) -> str:
    """Persist PageIndex output to jvspatial graph.

    Args:
        doc_name: Document identifier
        structure: PageIndex structure (tree of dicts with title, nodes, etc.)
        doc_description: Optional document-level description
        collection_name: Collection this document belongs to (default: "default")
        metadata: Custom key-value metadata for filtering at query time
        doc_url: Source URL of the document resource

    Returns:
        DocumentRootNode id for later retrieval/traversal
    """
    manager = get_database_manager()
    try:
        db = manager.get_database(PAGEINDEX_DB_NAME)
    except (ValueError, KeyError):
        logger.warning(
            f"PageIndex database '{PAGEINDEX_DB_NAME}' not registered. "
            "Call initialize_pageindex_database() first."
        )
        raise

    context = GraphContext(database=db)
    try:
        prev_context = get_default_context()
    except RuntimeError:
        prev_context = None
    try:
        set_default_context(context)

        root = DocumentRootNode(
            doc_name=doc_name,
            doc_description=doc_description,
            doc_url=doc_url,
            collection_name=collection_name,
            metadata=metadata,
        )
        await root.set_context(context)
        await root.save()

        top_level = await _tree_to_nodes(
            structure, doc_name, collection_name, context, None
        )
        for node in top_level:
            await root.connect(node, edge=DocumentContentEdge, direction="out")

        persisted_nodes = _count_structure_nodes(structure)
        logger.info(
            "Persisted document '%s' to PageIndex graph root=%s persisted_document_nodes=%s",
            doc_name,
            root.id,
            persisted_nodes,
        )
        return root.id
    finally:
        if prev_context is not None:
            set_default_context(prev_context)


async def tree_to_graph(
    pageindex_output: Dict[str, Any],
) -> str:
    """Convert PageIndex output to jvspatial graph and persist.

    Args:
        pageindex_output: Dict with doc_name, structure, and optionally doc_description,
            collection_name, metadata, doc_url

    Returns:
        DocumentRootNode id
    """
    doc_name = pageindex_output.get("doc_name", "")
    structure = pageindex_output.get("structure", [])
    doc_description = pageindex_output.get("doc_description")
    collection_name = pageindex_output.get("collection_name", "default")
    metadata = pageindex_output.get("metadata")
    doc_url = pageindex_output.get("doc_url")

    if not doc_name:
        raise ValueError("pageindex_output must contain 'doc_name'")
    if not structure:
        raise ValueError("pageindex_output must contain non-empty 'structure'")

    return await persist_structure(
        doc_name=doc_name,
        structure=structure,
        doc_description=doc_description,
        collection_name=collection_name,
        metadata=metadata,
        doc_url=doc_url,
    )
