"""PageIndexAction — graph-persisted Action for PageIndex retrieval and document operations.

Wraps the standalone functions in the pageindex package (search_documents,
list_documents, assimilate_document, delete_document) as methods on a
graph-persisted Action. This enables skill bundles to access PageIndex
via ActionResolver, separate from the directive-injection
PageIndexRetrievalInteractAction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class PageIndexAction(Action):
    """Action for PageIndex retrieval, ingestion, and document operations.

    Provides methods that delegate to the standalone functions in the
    pageindex package. Skill bundles resolve this action via
    ``visitor.action_resolver.resolve("PageIndexAction")`` and call its
    methods directly.

    Attribute defaults (strategy, limit, collection, etc.) serve as
    fallbacks — callers can override per-call via keyword arguments.
    """

    strategy: str = attribute(
        default="tree_search",
        description="Default retrieval strategy: 'tree_search', 'direct', or 'walker'",
    )
    limit: int = attribute(
        default=10,
        description="Default max results for search",
        ge=1,
    )
    collection: Optional[str] = attribute(
        default=None,
        description="Default collection name (defaults to agent_id when None)",
    )
    include_references: bool = attribute(
        default=True,
        description="Include doc_url references in search results",
    )
    only_enabled: bool = attribute(
        default=True,
        description="Skip DocumentNodes with enabled=false",
    )
    node_summary: bool = attribute(
        default=False,
        description="Generate LLM summaries during ingestion",
    )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_register(self) -> None:
        """Initialize PageIndex database when action is registered."""
        await super().on_register()
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)

    async def on_reload(self) -> None:
        """Re-init PageIndex database when action is reloaded."""
        await super().on_reload()
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_collection(self) -> str:
        """Resolve collection name from attribute, config, or agent_id."""
        cfg = self.config or {}
        return (
            self.collection
            or cfg.get("collection")
            or getattr(self, "agent_id", None)
            or "default"
        )

    def _resolve_model_action(self) -> Any:
        """Resolve the model action for tree_search LLM calls."""
        cfg = self.config or {}
        model_action_type = cfg.get("model_action_type", "OpenAILanguageModelAction")
        # Lazy resolution — avoids circular imports at module level
        try:
            return self.get_action(model_action_type)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        doc_name: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        max_summary_chars: Optional[int] = None,
        max_tree_prompt_tokens: Optional[int] = None,
        include_references: Optional[bool] = None,
        only_enabled: Optional[bool] = None,
        include: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search PageIndex documents using vectorless retrieval.

        All keyword arguments default to the action's attribute values
        when not explicitly provided.
        """
        from ..config import (
            get_pageindex_config,
            set_pageindex_candidate_k,
            set_pageindex_enable_lexical_index,
            set_pageindex_max_docs_for_tree_search,
            set_pageindex_max_summary_chars,
            set_pageindex_max_tree_prompt_tokens,
            set_pageindex_retrieval_excerpt_source,
        )
        from ..llm_bridge import set_pageindex_model_action
        from ..retrieval import search_documents

        cfg = self.config or {}

        resolved_strategy = strategy or cfg.get("strategy") or self.strategy
        resolved_limit = (
            limit if limit is not None else (cfg.get("limit") or self.limit)
        )
        resolved_collection = collection_name or self._resolve_collection()
        resolved_include_refs = (
            include_references
            if include_references is not None
            else self.include_references
        )
        resolved_only_enabled = (
            only_enabled if only_enabled is not None else self.only_enabled
        )

        # Push retrieval config for tree_search strategy
        if max_summary_chars is not None:
            set_pageindex_max_summary_chars(max_summary_chars)
        if max_tree_prompt_tokens is not None:
            set_pageindex_max_tree_prompt_tokens(max_tree_prompt_tokens)
        if cfg.get("enable_lexical_index") is not None:
            set_pageindex_enable_lexical_index(cfg["enable_lexical_index"])
        if cfg.get("candidate_k") is not None:
            set_pageindex_candidate_k(cfg["candidate_k"])
        if cfg.get("max_docs_for_tree_search") is not None:
            set_pageindex_max_docs_for_tree_search(cfg["max_docs_for_tree_search"])
        if cfg.get("retrieval_excerpt_source") is not None:
            set_pageindex_retrieval_excerpt_source(cfg["retrieval_excerpt_source"])

        model_action = self._resolve_model_action()
        prev_model_action = None
        try:
            if model_action:
                prev_model_action = get_pageindex_config().get("_model_action")
                set_pageindex_model_action(model_action)

            return await search_documents(
                query=query,
                doc_name=doc_name or cfg.get("doc_name"),
                strategy=resolved_strategy,
                limit=resolved_limit,
                model=cfg.get("model"),
                collection_name=resolved_collection,
                metadata_filter=metadata_filter,
                max_summary_chars=max_summary_chars,
                max_tree_prompt_tokens=max_tree_prompt_tokens,
                include_references=resolved_include_refs,
                only_enabled=resolved_only_enabled,
                include=include,
            )
        finally:
            if model_action:
                set_pageindex_model_action(prev_model_action)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        collection_name: Optional[str] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List documents in the PageIndex index."""
        from ..documents import list_documents as _list_documents

        return await _list_documents(
            collection_name=collection_name or self._resolve_collection(),
            metadata_filter=metadata_filter,
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def assimilate(
        self,
        doc: Any,
        *,
        doc_name: Optional[str] = None,
        model: Optional[str] = None,
        collection_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        doc_description: Optional[str] = None,
        doc_url: Optional[str] = None,
        convert_to_markdown: bool = False,
        ocr: bool = False,
    ) -> Dict[str, Any]:
        """Assimilate a document into the PageIndex index."""
        from ..documents import assimilate_document
        from ..llm_bridge import set_pageindex_model_action

        model_action = self._resolve_model_action()
        prev_model_action = None
        try:
            if model_action:
                from ..config import get_pageindex_config

                prev_model_action = get_pageindex_config().get("_model_action")
                set_pageindex_model_action(model_action)

            return await assimilate_document(
                doc=doc,
                doc_name=doc_name,
                model=model or "gpt-4o-mini",
                model_action=model_action,
                if_add_node_summary="yes" if self.node_summary else "no",
                collection_name=collection_name or self._resolve_collection(),
                metadata=metadata,
                doc_description=doc_description,
                doc_url=doc_url,
                convert_to_markdown=convert_to_markdown,
                ocr=ocr,
            )
        finally:
            if model_action:
                set_pageindex_model_action(prev_model_action)

    # ------------------------------------------------------------------
    # Removal
    # ------------------------------------------------------------------

    async def delete_document(
        self,
        doc_name: str,
        *,
        collection_name: Optional[str] = None,
    ) -> bool:
        """Delete a document and all its nodes from the PageIndex index."""
        from ..documents import delete_document as _delete_document

        return await _delete_document(
            doc_name=doc_name,
            collection_name=collection_name or self._resolve_collection(),
        )
