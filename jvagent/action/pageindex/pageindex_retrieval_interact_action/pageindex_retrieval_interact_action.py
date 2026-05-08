"""InteractAction for PageIndex vectorless RAG; delegates search to ``PageIndexAction``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.context import get_interaction, set_interaction
from jvagent.action.pageindex.prompts import (
    DIRECTIVE_TEMPLATE,
    DIRECTIVE_TEMPLATE_NO_REFS,
    DIRECTIVE_TEMPLATE_PLAIN,
)

from .. import llm_bridge
from ..config import initialize_pageindex_database
from ..pageindex_action.pageindex_action import PageIndexAction
from ..pageindex_action.runtime_config import (
    bool_from_config,
    format_page_range,
    get_ingestion_config,
    normalize_retrieval_excerpt_source,
    push_ingestion_config,
    push_retrieval_config,
)

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class PageIndexRetrievalInteractAction(InteractAction):
    """Runs interact retrieval by calling ``PageIndexAction.search`` on the same agent."""

    doc_name: Optional[str] = attribute(
        default=None,
        description="Optional document name to scope search to a single document",
    )
    limit: int = attribute(
        default=10,
        description="Number of search results to retrieve",
        ge=1,
    )
    weight: int = attribute(
        default=-75,
        description="Execution weight (runs after InteractRouter)",
    )
    directive: str = attribute(
        default=DIRECTIVE_TEMPLATE.template,
        description="Template for formatting the directive. Placeholder: {results}",
    )
    directive_no_refs: str = attribute(
        default=DIRECTIVE_TEMPLATE_NO_REFS.template,
        description="Template for formatting the directive without references. Placeholder: {results}",
    )
    directive_plain: str = attribute(
        default=DIRECTIVE_TEMPLATE_PLAIN.template,
        description="Template for formatting the directive in plain text. Placeholder: {results}",
    )
    strategy: str = attribute(
        default="tree_search",
        description="Retrieval strategy: 'tree_search', 'direct', or 'walker'",
    )
    model: Optional[str] = attribute(
        default=None,
        description="LLM model for tree_search (default: PAGEINDEX_TREE_SEARCH_MODEL or gpt-4o-mini)",
    )
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction type for tree_search (enables observability)",
    )
    parameters: List[Dict[str, Any]] = attribute(
        default=[
            {
                "condition": "There is no data in the context or anywhere else in the prompt that can answer the user request",
                "response": "Answer based on your own knowledge but mention that the information might be inaccurate or out of date and encourage them to seek external sources of information.",
            }
        ],
        description="Parameters for behavioral guidance",
    )
    retrieval_excerpt_source: str = attribute(
        default="summary",
        description="What to show in tree_search prompts and directive excerpts: 'summary' or 'text'.",
    )
    collection: Optional[str] = attribute(
        default=None,
        description="Collection name (default: agent_id). Override via context or config.",
    )
    metadata_filter: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Optional key-value filter to narrow search by document metadata",
    )
    max_summary_chars: Optional[int] = attribute(
        default=None,
        description="Max chars per node summary in tree prompt (default: 300).",
    )
    max_tree_prompt_tokens: Optional[int] = attribute(
        default=None,
        description="Max tokens for tree in tree-search prompt (default: 16000).",
    )
    include_references: bool = attribute(
        default=True,
        description="When True, render numbered source references in the directive.",
    )
    only_enabled: bool = attribute(
        default=True,
        description="When True, retrieval skips DocumentNodes with enabled=false.",
    )
    retrieval_include: Optional[List[str]] = attribute(
        default=None,
        description="Optional extra fields per hit (same as REST search body `include`).",
    )

    async def _get_pageindex_core(self) -> Optional[PageIndexAction]:
        action = await self.get_action("PageIndexAction")
        if action and isinstance(action, PageIndexAction):
            return action
        return None

    def _resolve_collection(self) -> str:
        return (
            self.collection
            or (self.config.get("collection") if self.config else None)
            or getattr(self, "agent_id", None)
            or "default"
        )

    
    def _retrieval_runtime_config(self, visitor: InteractWalker) -> Dict[str, Any]:
        cfg = self.config or {}
        max_summary_chars = (
            cfg.get("max_summary_chars")
            if cfg.get("max_summary_chars") is not None
            else self.max_summary_chars
        )
        max_tree_prompt_tokens = (
            cfg.get("max_tree_prompt_tokens")
            if cfg.get("max_tree_prompt_tokens") is not None
            else self.max_tree_prompt_tokens
        )
        only_en = (
            bool_from_config(cfg["only_enabled"], self.only_enabled)
            if "only_enabled" in cfg and cfg["only_enabled"] is not None
            else self.only_enabled
        )
        inc = cfg.get("include")
        if inc is None:
            inc = cfg.get("include_fields")
        include_list = self._coerce_include_list(inc)
        if include_list is None:
            include_list = self._coerce_include_list(self.retrieval_include)

        return {
            "limit": cfg.get("limit") if cfg.get("limit") is not None else self.limit,
            "strategy": cfg.get("strategy") or self.strategy,
            "model": cfg.get("model") or self.model,
            "doc_name": self.doc_name or cfg.get("doc_name"),
            "collection_name": self._resolve_collection(),
            "metadata_filter": self.metadata_filter,
            "max_summary_chars": max_summary_chars,
            "max_tree_prompt_tokens": max_tree_prompt_tokens,
            "retrieval_excerpt_source": self._resolve_retrieval_excerpt_source(),
            "enable_lexical_index": cfg.get("enable_lexical_index"),
            "candidate_k": cfg.get("candidate_k"),
            "max_docs_for_tree_search": cfg.get("max_docs_for_tree_search"),
            "only_enabled": only_en,
            "include": include_list,
        }

    @staticmethod
    def _coerce_include_list(val: Any) -> Optional[List[str]]:
        if val is None:
            return None
        if isinstance(val, list):
            out = [str(x).strip() for x in val if str(x).strip()]
            return out or None
        if isinstance(val, str):
            out = [s.strip() for s in val.split(",") if s.strip()]
            return out or None
        return None

    def _resolve_retrieval_excerpt_source(self) -> str:
        cfg = self.config or {}
        if (
            "retrieval_excerpt_source" in cfg
            and cfg["retrieval_excerpt_source"] is not None
        ):
            return normalize_retrieval_excerpt_source(
                cfg["retrieval_excerpt_source"], "summary"
            )
        return normalize_retrieval_excerpt_source(
            self.retrieval_excerpt_source, "summary"
        )

    async def execute(self, visitor: InteractWalker) -> None:
        interaction = visitor.interaction
        if not interaction:
            logger.warning("PageIndexRetrievalInteractAction: No interaction")
            return

        core = await self._get_pageindex_core()
        if not core:
            logger.error(
                "PageIndexRetrievalInteractAction: PageIndexAction not configured "
                "for this agent; add jvagent/pageindex_action"
            )
            return

        ingestion = get_ingestion_config(core.config, core.node_summary)
        push_ingestion_config(ingestion)
        model_action = await core.get_model_action()
        prev_interaction = get_interaction()
        try:
            set_interaction(interaction)
            if model_action:
                llm_bridge.set_pageindex_model_action(model_action)

            query = self._get_search_query(interaction)
            if not query:
                logger.debug("PageIndexRetrievalInteractAction: No query")
                return

            initialize_pageindex_database()
            rtc = self._retrieval_runtime_config(visitor)
            push_retrieval_config(
                {
                    "max_summary_chars": rtc["max_summary_chars"],
                    "max_tree_prompt_tokens": rtc["max_tree_prompt_tokens"],
                    "enable_lexical_index": rtc["enable_lexical_index"],
                    "candidate_k": rtc["candidate_k"],
                    "max_docs_for_tree_search": rtc["max_docs_for_tree_search"],
                    "retrieval_excerpt_source": rtc["retrieval_excerpt_source"],
                }
            )
            results = await core.search(
                query,
                doc_name=rtc["doc_name"],
                strategy=rtc["strategy"],
                limit=rtc["limit"],
                collection_name=rtc["collection_name"],
                metadata_filter=rtc["metadata_filter"],
                max_summary_chars=rtc["max_summary_chars"],
                max_tree_prompt_tokens=rtc["max_tree_prompt_tokens"],
                include_references=self._resolve_include_references(),
                only_enabled=rtc["only_enabled"],
                include=rtc["include"],
                model=rtc["model"],
                enable_lexical_index=rtc["enable_lexical_index"],
                candidate_k=rtc["candidate_k"],
                max_docs_for_tree_search=rtc["max_docs_for_tree_search"],
                retrieval_excerpt_source=rtc["retrieval_excerpt_source"],
                visitor=visitor,
            )

            if results:
                directive = self._format_directive(results)
                await visitor.add_directive(directive)
                logger.debug(
                    "PageIndexRetrievalInteractAction: Added directive with %s results",
                    len(results),
                )
            if self.parameters:
                await visitor.add_parameters(self.parameters)

        except Exception as e:
            logger.error(
                "PageIndexRetrievalInteractAction: Error: %s",
                e,
                exc_info=True,
            )
        finally:
            llm_bridge.set_pageindex_model_action(None)
            set_interaction(prev_interaction)

    def _get_search_query(self, interaction: "Interaction") -> Optional[str]:
        query = interaction.utterance or interaction.interpretation
        return query.strip() if query else None

    def _resolve_include_references(self) -> bool:
        if self.config and "include_references" in self.config:
            return bool_from_config(self.config["include_references"], True)
        return self.include_references

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        if not self._resolve_include_references():
            return self._format_directive_plain(results)
        return self._format_directive_with_references(results)

    def _format_directive_plain(self, results: List[Dict[str, Any]]) -> str:
        parts = []
        for r in results:
            content = r.get("content", r.get("text", r.get("title", "")))
            title = r.get("title", "")
            doc = r.get("doc_name", "")
            prefix = f"[{doc}] {title}: " if doc or title else ""
            parts.append(f"- {prefix}{content}")
        return self.directive_plain.format(results="\n".join(parts))

    def _format_directive_with_references(self, results: List[Dict[str, Any]]) -> str:
        source_to_ref: Dict[tuple, int] = {}
        ref_entries: List[str] = []
        has_ref_metadata = False

        for r in results:
            page_range = format_page_range(r)
            url = r.get("doc_url")
            doc = r.get("doc_name", "")
            if page_range or url:
                has_ref_metadata = True

            ref_key = (doc or "", page_range or "", url or "")
            if ref_key not in source_to_ref:
                ref_num = len(source_to_ref) + 1
                source_to_ref[ref_key] = ref_num
                ref_str = f"[{ref_num}]"
                if doc:
                    ref_str += f" {doc}"
                if page_range:
                    ref_str += f", {page_range}"
                if url:
                    ref_str += f". {url}" if doc or page_range else f" {url}"
                ref_entries.append(ref_str)

        excerpt_lines: List[str] = []
        for r in results:
            content = r.get("content", r.get("text", r.get("title", "")))
            title = r.get("title", "")
            doc = r.get("doc_name", "")
            page_range = format_page_range(r)
            url = r.get("doc_url")
            ref_key = (doc or "", page_range or "", url or "")
            ref_num = source_to_ref[ref_key]
            label = f"[{doc}] {title}" if doc or title else "Excerpt"
            excerpt_lines.append(f"[{ref_num}] {label}: {content}")

        results_str = "\n".join(excerpt_lines)
        if has_ref_metadata and ref_entries:
            return self.directive.format(
                results=results_str, references="\n".join(ref_entries)
            )
        return self.directive_no_refs.format(results=results_str)
