"""DocumentWalker for query-aware graph traversal.

Traverses DocumentNode graph, collecting nodes that match the search query
via text/substring matching (no embeddings, no vector store).
"""

import logging
import re
from typing import Optional

from jvspatial.core import Walker, on_visit

from .models import DocumentContentEdge, DocumentNode, DocumentRootNode

logger = logging.getLogger(__name__)


class DocumentWalker(Walker):
    """Walker that traverses document graph and collects nodes matching a query.

    At each DocumentNode, checks if title, text, or summary matches the query
    (case-insensitive substring or regex). Matching nodes are added to the report.
    Follows DocumentContentEdge to traverse parent-child hierarchy.
    Stops traversal when report size reaches limit (early termination).
    """

    def __init__(self, query: str = "", limit: Optional[int] = None, **kwargs):
        super().__init__(**kwargs)
        self._query = (query or "").strip()
        self._query_lower = self._query.lower()
        self._query_regex: Optional[re.Pattern] = None
        self._limit = limit
        if self._query:
            try:
                self._query_regex = re.compile(re.escape(self._query), re.IGNORECASE)
            except re.error:
                self._query_regex = None

    def _at_limit(self) -> bool:
        """True if report has reached limit (no more items should be added)."""
        if self._limit is None:
            return False
        return len(self._report) >= self._limit

    def _matches(self, node: DocumentNode) -> bool:
        """Check if node content matches the query."""
        if not self._query:
            return True

        fields = [
            getattr(node, "title", "") or "",
            getattr(node, "text", "") or "",
            getattr(node, "summary", "") or "",
            getattr(node, "prefix_summary", "") or "",
        ]
        combined = " ".join(str(f) for f in fields)

        if self._query_regex:
            return bool(self._query_regex.search(combined))
        return self._query_lower in combined.lower()

    @on_visit(DocumentNode)
    async def on_document_node(self, here: DocumentNode) -> None:
        """Visit DocumentNode: if it matches query, add to report; queue children."""
        if self._at_limit():
            return
        if self._matches(here):
            content = here.summary or here.text or here.title or ""
            await self.report(
                {
                    "node_id": here.id,
                    "title": here.title,
                    "text": here.text,
                    "summary": here.summary,
                    "doc_name": here.doc_name,
                    "structure": here.structure,
                    "content": content[:2000] if content else "",
                }
            )
        if self._at_limit():
            return
        children = await here.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        if children:
            await self.visit(children)

    @on_visit(DocumentRootNode)
    async def on_root_node(self, here: DocumentRootNode) -> None:
        """Visit DocumentRootNode: queue top-level section nodes."""
        children = await here.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        if children:
            await self.visit(children)
