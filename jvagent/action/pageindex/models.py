"""jvspatial entity models for PageIndex document graph.

DocumentNode, DocumentContentEdge, and DocumentRootNode extend jvspatial Node/Edge
for graph-based persistence of document structure.
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute

from .config import get_pageindex_retrieval_excerpt_source

_MAX_CONTENT_CHARS = 2000


class DocumentRootNode(Node):
    """Root node for a document; links to top-level section nodes.

    One per document. Provides doc-level metadata and entry point for traversal.

    Attributes:
        doc_name: Document identifier (e.g., filename or title)
        doc_description: Optional document-level description
        doc_url: Source URL of the document resource
        collection_name: Collection this document belongs to (typically agent_id)
        metadata: Custom key-value metadata for filtering at query time
    """

    doc_name: str = attribute(
        default="",
        description="Document identifier (e.g., filename or title)",
    )
    doc_description: Optional[str] = attribute(
        default=None,
        description="Optional document-level description",
    )
    doc_url: Optional[str] = attribute(
        default=None,
        description="Source URL of the document resource",
    )
    collection_name: str = attribute(
        default="default",
        description="Collection this document belongs to (typically agent_id)",
    )
    metadata: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Custom key-value metadata for filtering at query time",
    )
    chunks: Optional[int] = attribute(
        default=None,
        description="Number of DocumentNode chunks for this document",
    )


class DocumentNode(Node):
    """Graph node representing a document section/chunk.

    Maps to PageIndex structure items: title, text, summary, page ranges, hierarchy.

    Attributes:
        title: Section title
        node_id: PageIndex node_id (e.g., "0001", "0002")
        text: Full text content of the section
        summary: Optional summary (for sections with children)
        prefix_summary: Optional prefix summary
        physical_index: Page number where section starts (PDF)
        start_index: Start page index (1-based)
        end_index: End page index (1-based)
        structure: Hierarchy code (e.g., "1.2.3")
        doc_name: Document this node belongs to
        line_num: Line number in source (for Markdown)
    """

    title: str = attribute(default="", description="Section title")
    node_id: str = attribute(default="", description="PageIndex node_id")
    text: str = attribute(default="", description="Full text content")
    summary: Optional[str] = attribute(default=None, description="Section summary")
    prefix_summary: Optional[str] = attribute(
        default=None, description="Prefix summary for parent sections"
    )
    physical_index: Optional[int] = attribute(
        default=None, description="Page number where section starts"
    )
    start_index: Optional[int] = attribute(default=None, description="Start page index")
    end_index: Optional[int] = attribute(default=None, description="End page index")
    structure: str = attribute(default="", description="Hierarchy code (e.g., 1.2.3)")
    doc_name: str = attribute(default="", description="Document this node belongs to")
    collection_name: str = attribute(
        default="default",
        description="Collection this node belongs to",
    )
    line_num: Optional[int] = attribute(
        default=None, description="Line number in source (Markdown)"
    )
    enabled: bool = attribute(
        default=True,
        description="When False, omit from retrieval if only_enabled (unless overridden)",
    )
    content_type: Optional[str] = attribute(
        default=None,
        description="Structural or shape tag from enriched markdown (e.g. substantive, table_of_contents)",
    )
    hierarchy: Optional[List[str]] = attribute(
        default=None,
        description="Breadcrumb of section titles from root to this node",
    )


def node_enabled(node: "DocumentNode") -> bool:
    """True if chunk is eligible for RAG when only_enabled is used (default True)."""
    v = getattr(node, "enabled", None)
    if v is None:
        return True
    return bool(v)


class DocumentContentEdge(Edge):
    """Edge connecting parent DocumentNode to child DocumentNode.

    Represents parent-child hierarchy in document structure.
    Direction: parent (source) -> child (target).
    """


def node_to_result(
    node: DocumentNode, excerpt_source: Optional[str] = None
) -> Dict[str, Any]:
    """Build a standard result dict from a DocumentNode.

    excerpt_source: 'summary' (prefer summary/prefix_summary, else text), 'text'
        (prefer body text, else summary), or None to use
        get_pageindex_retrieval_excerpt_source() (default 'summary').
    """
    mode = (
        excerpt_source
        if excerpt_source is not None
        else get_pageindex_retrieval_excerpt_source()
    )
    if mode == "text":
        content = node.text or node.summary or node.title or ""
    else:
        content = (
            (node.summary or node.prefix_summary or "").strip()
            or node.text
            or node.title
            or ""
        )
    return {
        "node_id": node.id,
        "title": node.title,
        "text": node.text,
        "summary": node.summary,
        "doc_name": node.doc_name,
        "structure": node.structure,
        "content": content[:_MAX_CONTENT_CHARS] if content else "",
        "start_index": node.start_index,
        "end_index": node.end_index,
        "physical_index": node.physical_index,
        "enabled": node_enabled(node),
    }


_INCLUDE_ATTR_GETTERS: Dict[str, Any] = {
    "title": lambda n: n.title,
    "text": lambda n: n.text,
    "summary": lambda n: n.summary,
    "prefix_summary": lambda n: n.prefix_summary,
    "doc_name": lambda n: n.doc_name,
    "structure": lambda n: n.structure,
    "line_num": lambda n: n.line_num,
    "start_index": lambda n: n.start_index,
    "end_index": lambda n: n.end_index,
    "physical_index": lambda n: n.physical_index,
    "enabled": lambda n: node_enabled(n),
    "content_type": lambda n: getattr(n, "content_type", None),
    "hierarchy": lambda n: getattr(n, "hierarchy", None),
    "pageindex_node_id": lambda n: n.node_id,
}


def copy_included_fields(
    node: DocumentNode,
    base: Dict[str, Any],
    include: Optional[List[str]],
) -> Dict[str, Any]:
    """Merge whitelisted metadata into a search result row (deep copy; never duplicates ``content``)."""
    if not include:
        return base
    out = dict(base)
    for key in include:
        if key == "content" or key in out:
            continue
        getter = _INCLUDE_ATTR_GETTERS.get(key)
        if not getter:
            continue
        val = getter(node)
        out[key] = deepcopy(val)
    return out
