"""PageIndex action module for document assimilation and vectorless RAG.

Wraps PageIndex for PDF/Markdown document indexing, persists structure to a
separate jvspatial graph database, and provides vectorless retrieval via
graph traversal and text filtering.
"""

# Strategic override: inject LLM bridge so core uses jvagent model for observability
import sys
from jvagent.action.pageindex import llm_override

sys.modules["jvagent.action.pageindex.core.utils"] = llm_override.override_module

from jvagent.action.pageindex import endpoints  # noqa: F401

from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_config,
    initialize_pageindex_database,
)
from jvagent.action.pageindex.models import DocumentContentEdge, DocumentNode, DocumentRootNode
from jvagent.action.pageindex.adapter import persist_structure, tree_to_graph
from jvagent.action.pageindex.documents import assimilate_document, get_document_root
from jvagent.action.pageindex.retrieval import search_documents
from jvagent.action.pageindex.document_walker import DocumentWalker
from .user_model_retrieval_interact_action import UserModelRetrievalInteractAction

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
    "UserModelRetrievalInteractAction",
]
