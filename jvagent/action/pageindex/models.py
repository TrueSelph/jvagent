"""jvspatial entity models for PageIndex document graph.

DocumentNode, DocumentContentEdge, and DocumentRootNode extend jvspatial Node/Edge
for graph-based persistence of document structure.
"""

from typing import Any, Dict, Optional

from jvspatial.core import Edge, Node
from jvspatial.core.annotations import attribute


class DocumentRootNode(Node):
    """Root node for a document; links to top-level section nodes.

    One per document. Provides doc-level metadata and entry point for traversal.

    Attributes:
        doc_name: Document identifier (e.g., filename or title)
        doc_description: Optional document-level description
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
    collection_name: str = attribute(
        default="default",
        description="Collection this document belongs to (typically agent_id)",
    )
    metadata: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Custom key-value metadata for filtering at query time",
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
    structure: str = attribute(
        default="", description="Hierarchy code (e.g., 1.2.3)"
    )
    doc_name: str = attribute(
        default="", description="Document this node belongs to"
    )
    collection_name: str = attribute(
        default="default",
        description="Collection this node belongs to",
    )
    line_num: Optional[int] = attribute(
        default=None, description="Line number in source (Markdown)"
    )


class DocumentContentEdge(Edge):
    """Edge connecting parent DocumentNode to child DocumentNode.

    Represents parent-child hierarchy in document structure.
    Direction: parent (source) -> child (target).
    """

    pass
