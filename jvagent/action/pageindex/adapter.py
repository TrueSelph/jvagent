"""Tree-to-graph adapter for PageIndex output.

Transforms PageIndex tree structure into jvspatial Node/Edge graph
and persists to the PageIndex database.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.context import GraphContext, get_default_context, set_default_context
from jvspatial.db import get_database_manager

from .config import PAGEINDEX_DB_NAME
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode

logger = logging.getLogger(__name__)


def _extract_node_data(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract DocumentNode fields from a PageIndex structure item."""
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
    }


async def _tree_to_nodes(
    structure: Any,
    doc_name: str,
    context: GraphContext,
    parent_node: Optional[DocumentNode],
) -> List[DocumentNode]:
    """Recursively convert tree structure to DocumentNodes and edges.

    Args:
        structure: Tree node (dict) or list of tree nodes
        doc_name: Document identifier
        context: GraphContext for persistence
        parent_node: Parent DocumentNode (or None for root-level)

    Returns:
        List of created DocumentNode instances
    """
    created: List[DocumentNode] = []

    async def process_item(
        item: Dict[str, Any], parent: Optional[DocumentNode]
    ) -> DocumentNode:
        data = _extract_node_data(item)
        data["doc_name"] = doc_name

        node = DocumentNode(**data)
        await node.set_context(context)
        await node.save()
        created.append(node)

        if parent:
            await parent.connect(node, edge=DocumentContentEdge, direction="out")

        children = item.get("nodes") or []
        for child_item in children:
            await process_item(child_item, node)

        return node

    if isinstance(structure, list):
        for item in structure:
            if isinstance(item, dict):
                await process_item(item, parent_node)
    elif isinstance(structure, dict):
        await process_item(structure, parent_node)

    return created


async def persist_structure(
    doc_name: str,
    structure: Any,
    doc_description: Optional[str] = None,
) -> str:
    """Persist PageIndex output to jvspatial graph.

    Args:
        doc_name: Document identifier
        structure: PageIndex structure (tree of dicts with title, nodes, etc.)
        doc_description: Optional document-level description

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
    prev_context = get_default_context()
    try:
        set_default_context(context)

        root = DocumentRootNode(
            doc_name=doc_name,
            doc_description=doc_description,
        )
        await root.set_context(context)
        await root.save()

        top_level = await _tree_to_nodes(structure, doc_name, context, None)
        for node in top_level:
            await root.connect(node, edge=DocumentContentEdge, direction="out")

        logger.info(
            f"Persisted document '{doc_name}' to PageIndex graph (root={root.id})"
        )
        return root.id
    finally:
        set_default_context(prev_context)


async def tree_to_graph(
    pageindex_output: Dict[str, Any],
) -> str:
    """Convert PageIndex output to jvspatial graph and persist.

    Args:
        pageindex_output: Dict with doc_name, structure, and optionally doc_description

    Returns:
        DocumentRootNode id
    """
    doc_name = pageindex_output.get("doc_name", "")
    structure = pageindex_output.get("structure", [])
    doc_description = pageindex_output.get("doc_description")

    if not doc_name:
        raise ValueError("pageindex_output must contain 'doc_name'")
    if not structure:
        raise ValueError("pageindex_output must contain non-empty 'structure'")

    return await persist_structure(
        doc_name=doc_name,
        structure=structure,
        doc_description=doc_description,
    )
