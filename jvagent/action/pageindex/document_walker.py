"""DocumentWalker for query-aware graph traversal.

Traverses DocumentNode graph, collecting nodes that match the search query
via text/substring matching (no embeddings, no vector store).
"""

import logging
import re
from typing import Optional

from jvspatial.core import Walker, on_visit

from .models import DocumentContentEdge, DocumentNode, DocumentRootNode, node_to_result

logger = logging.getLogger(__name__)

_MATCH_FIELDS = ("title", "text", "summary", "prefix_summary")


class DocumentWalker(Walker):
    """Walker that traverses document graph and collects nodes matching a query.

    At each DocumentNode, checks if title, text, or summary matches the query
    (case-insensitive substring). Matching nodes are added to the report.
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
            self._query_regex = re.compile(re.escape(self._query), re.IGNORECASE)

    def _at_limit(self) -> bool:
        """True if report has reached limit (no more items should be added)."""
        if self._limit is None:
            return False
        return len(self._report) >= self._limit

    def _matches(self, node: DocumentNode) -> bool:
        """Check if node content matches the query (short-circuits per field)."""
        if not self._query:
            return True
        for field_name in _MATCH_FIELDS:
            value = getattr(node, field_name, None)
            if not value:
                continue
            if self._query_regex:
                if self._query_regex.search(value):
                    return True
            elif self._query_lower in value.lower():
                return True
        return False

    @on_visit(DocumentNode)
    async def on_document_node(self, here: DocumentNode) -> None:
        """Visit DocumentNode: if it matches query, add to report; queue children."""
        if self._at_limit():
            return
        if self._matches(here):
            await self.report(node_to_result(here))
        if self._at_limit():
            return
        children = await here.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        if children:
            await self.visit(children)

    @on_visit(DocumentRootNode)
    async def on_root_node(self, here: DocumentRootNode) -> None:
        """Visit DocumentRootNode: queue top-level section nodes."""
        if self._at_limit():
            return
        children = await here.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        if children:
            await self.visit(children)
