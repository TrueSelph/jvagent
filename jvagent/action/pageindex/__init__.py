"""PageIndex action module for document assimilation and vectorless RAG.

Wraps PageIndex for PDF/Markdown document indexing, persists structure to a
separate jvspatial graph database, and provides vectorless retrieval via
graph traversal and text filtering.
"""

# Strategic override: inject LLM bridge so core uses jvagent model for observability
import sys

from . import llm_override

sys.modules["jvagent.action.pageindex.core.utils"] = llm_override.override_module

from . import endpoints  # noqa: F401
from .adapter import persist_structure, tree_to_graph
from .config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_config,
    initialize_pageindex_database,
)
from .document_walker import DocumentWalker
from .documents import (
    assimilate_document,
    export_documents,
    get_document_root,
    import_documents,
)
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode, node_to_result
from .pageindex_action import PageIndexAction
from .pageindex_retrieval_interact_action import PageIndexRetrievalInteractAction
from .retrieval import search_documents

__all__ = [
    "PAGEINDEX_DB_NAME",
    "get_pageindex_config",
    "initialize_pageindex_database",
    "DocumentNode",
    "DocumentContentEdge",
    "DocumentRootNode",
    "node_to_result",
    "persist_structure",
    "tree_to_graph",
    "assimilate_document",
    "get_document_root",
    "search_documents",
    "DocumentWalker",
    "PageIndexRetrievalInteractAction",
    "PageIndexAction",
    "export_documents",
    "import_documents",
]
