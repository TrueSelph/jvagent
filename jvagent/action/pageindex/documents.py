"""PageIndex document operations.

Wraps vendored PageIndex core (page_index, md_to_tree) for document assimilation,
persisting the resulting structure to the jvspatial graph database.
"""

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from jvspatial.core.context import GraphContext, get_default_context, set_default_context
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
from .core import page_index, md_to_tree
from .llm_bridge import set_pageindex_model_action
from .models import DocumentRootNode

logger = logging.getLogger(__name__)


def _to_yes_no(value: Any, default: bool) -> str:
    """Normalize bool-like value to yes/no. None -> default; yes/true/1 -> yes; else no."""
    if value is None:
        return "yes" if default else "no"
    v = str(value).lower().strip()
    return "yes" if v in ("yes", "true", "1") else "no"


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

    Returns:
        Dict with doc_name, structure, doc_description (if requested), _root_id (if persist)
    """
    initialize_pageindex_database()

    # Core expects a valid model string (tiktoken.encoding_for_model fails on None)
    model = model or "gpt-4o-mini"

    # Normalize: true/yes/1 -> "yes", false/no/0 -> "no" for core; use config when None
    if_add_node_summary = _to_yes_no(
        if_add_node_summary,
        get_pageindex_node_summary() if if_add_node_summary is None else False,
    )
    if_add_node_text = _to_yes_no(
        if_add_node_text,
        get_pageindex_node_text() if if_add_node_text is None else True,
    )
    if_add_doc_description = _to_yes_no(
        if_add_doc_description,
        get_pageindex_doc_description() if if_add_doc_description is None else False,
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
            loop = asyncio.get_event_loop()
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
            root_id = await tree_to_graph(result)
            result["_root_id"] = root_id
            logger.info(f"Assimilated document '{name}' (root={root_id})")

        return result
    finally:
        if model_action:
            set_pageindex_model_action(None)


async def get_document_root(doc_name: str) -> Optional[DocumentRootNode]:
    """Get DocumentRootNode by doc_name."""
    initialize_pageindex_database()
    context = _get_pageindex_context()
    prev = get_default_context()
    try:
        set_default_context(context)
        roots = await DocumentRootNode.find({"context.doc_name": doc_name})
        return roots[0] if roots else None
    finally:
        set_default_context(prev)


async def list_documents() -> List[Dict[str, Any]]:
    """List all documents in the PageIndex graph."""
    initialize_pageindex_database()
    context = _get_pageindex_context()
    prev = get_default_context()
    try:
        set_default_context(context)
        roots = await DocumentRootNode.find()
        return [
            {
                "doc_name": r.doc_name,
                "doc_description": r.doc_description,
                "root_id": r.id,
            }
            for r in roots
        ]
    finally:
        set_default_context(prev)


async def delete_document(doc_name: str) -> bool:
    """Delete a document and all its nodes from the PageIndex graph."""
    initialize_pageindex_database()
    root = await get_document_root(doc_name)
    if not root:
        return False

    context = _get_pageindex_context()
    prev = get_default_context()
    try:
        set_default_context(context)
        await root.delete()
        logger.info(f"Deleted document '{doc_name}'")
        return True
    finally:
        set_default_context(prev)
