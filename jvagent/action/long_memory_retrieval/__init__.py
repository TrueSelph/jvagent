"""Long memory retrieval action. Searches the user's profile collection in PageIndex.

Uses PageIndex infrastructure to retrieve context from the
``user_long_memory_{user_id}`` document. The pageindex package applies the LLM
bridge monkey-patch on import; load it first via a no-op import.
"""

import jvagent.action.pageindex  # noqa: F401  # ensures package init / llm_override
from jvagent.action.pageindex.adapter import persist_structure, tree_to_graph
from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_config,
    initialize_pageindex_database,
)
from jvagent.action.pageindex.document_walker import DocumentWalker
from jvagent.action.pageindex.documents import assimilate_document, get_document_root
from jvagent.action.pageindex.models import (
    DocumentContentEdge,
    DocumentNode,
    DocumentRootNode,
)
from jvagent.action.pageindex.retrieval import search_documents

from .long_memory_retrieval_interact_action import UserLongMemoryRetrievalInteractAction

__all__ = [
    "PAGEINDEX_DB_NAME",
    "get_pageindex_config",
    "initialize_pageindex_database",
    "DocumentNode",
    "DocumentContentEdge",
    "DocumentRootNode",
    "persist_structure",
    "tree_to_graph",
    "assimilate_document",
    "get_document_root",
    "search_documents",
    "DocumentWalker",
    "UserLongMemoryRetrievalInteractAction",
]
