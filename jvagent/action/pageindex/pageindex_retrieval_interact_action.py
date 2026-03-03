"""PageIndexRetrievalInteractAction for vectorless RAG.

Uses PageIndex graph with LLM-based tree search (default) or text filtering.
No embeddings, no vector store.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.context import get_interaction, set_interaction
from jvagent.action.pageindex.config import (
    initialize_pageindex_database,
    set_pageindex_doc_description,
    set_pageindex_max_summary_chars,
    set_pageindex_max_token_num_each_node,
    set_pageindex_max_tree_prompt_tokens,
    set_pageindex_node_summary,
    set_pageindex_node_text,
    set_pageindex_summary_token_threshold,
)
from jvagent.action.pageindex.llm_bridge import set_pageindex_model_action
from jvagent.action.pageindex.prompts import DIRECTIVE_TEMPLATE
from jvagent.action.pageindex.retrieval import search_documents

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


def _bool_from_config(value: Any, default: bool) -> bool:
    """Convert config value to bool. None -> default; yes/true/1 -> True; else False."""
    if value is None:
        return default
    v = str(value).lower().strip()
    return v in ("yes", "true", "1")


def _push_ingestion_config(ingestion: Dict[str, Any]) -> None:
    """Push ingestion config values to config module."""
    set_pageindex_node_summary(ingestion.get("node_summary", False))
    set_pageindex_node_text(ingestion.get("node_text", True))
    set_pageindex_doc_description(ingestion.get("doc_description", False))
    set_pageindex_max_token_num_each_node(ingestion.get("max_token_num_each_node"))
    set_pageindex_summary_token_threshold(ingestion.get("summary_token_threshold"))


def _push_retrieval_config(retrieval: Dict[str, Any]) -> None:
    """Push retrieval config values to config module."""
    if "max_summary_chars" in retrieval and retrieval["max_summary_chars"] is not None:
        set_pageindex_max_summary_chars(retrieval["max_summary_chars"])
    if (
        "max_tree_prompt_tokens" in retrieval
        and retrieval["max_tree_prompt_tokens"] is not None
    ):
        set_pageindex_max_tree_prompt_tokens(retrieval["max_tree_prompt_tokens"])


def _get_ingestion_config(
    config: Dict[str, Any], node_summary_attr: bool
) -> Dict[str, Any]:
    """Resolve ingestion config from action config (with attribute fallback for node_summary)."""
    cfg = config or {}
    node_summary = (
        _bool_from_config(cfg["node_summary"], False)
        if "node_summary" in cfg
        else node_summary_attr
    )
    return {
        "node_summary": node_summary,
        "node_text": _bool_from_config(cfg.get("node_text"), True),
        "doc_description": _bool_from_config(cfg.get("doc_description"), False),
        "max_token_num_each_node": cfg.get("max_token_num_each_node"),
        "summary_token_threshold": cfg.get("summary_token_threshold")
        or cfg.get("max_node_tokens"),
    }


async def ensure_ingestion_config_for_agent(agent_id: str) -> None:
    """Push ingestion config from agent's PageIndex action to config module.

    Used when REST ingest does not receive if_add_node_summary in the form.
    Resolves config from cached actions; falls back to node_summary=True when
    cache miss or no PageIndex action (agent-scoped routes assume PageIndex).
    """
    from jvagent.core.cache import get_cached_actions

    actions = await get_cached_actions(agent_id, enabled_only=True)
    for action in actions or []:
        if isinstance(action, PageIndexRetrievalInteractAction):
            config = getattr(action, "config", None) or {}
            node_summary_attr = getattr(action, "node_summary", False)
            ingestion = _get_ingestion_config(config, node_summary_attr)
            _push_ingestion_config(ingestion)
            return
    # Fallback: default to summaries for agent-scoped routes
    _push_ingestion_config(
        {
            "node_summary": True,
            "node_text": True,
            "doc_description": False,
            "max_token_num_each_node": None,
            "summary_token_threshold": None,
        }
    )


class PageIndexRetrievalInteractAction(InteractAction):
    """InteractAction that retrieves context from PageIndex graph (vectorless).

    Uses LLM-based tree search by default (PageIndex recommended approach), with
    fallback to direct or walker strategies. No VectorStore, no embeddings.
    1. Uses interaction's utterance (or interpretation) as search query
    2. Searches PageIndex document graph via tree_search, direct, or walker
    3. Formats results into directive for PersonaAction
    """

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
        default=DIRECTIVE_TEMPLATE,
        description="Template for formatting the directive. Placeholder: {results}",
    )
    strategy: str = attribute(
        default="tree_search",
        description="Retrieval strategy: 'tree_search' (LLM reasoning, recommended), "
        "'direct' (database.find), or 'walker' (graph traversal)",
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
    node_summary: bool = attribute(
        default=False,
        description="When True, generate node summaries during ingestion (if_add_node_summary='yes'). "
        "Required for tree search to work well.",
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
        description="Max chars per node summary in tree prompt (default: 300). "
        "Truncates summaries for compact LLM context.",
    )
    max_tree_prompt_tokens: Optional[int] = attribute(
        default=None,
        description="Max tokens for tree in tree-search prompt (default: 16000). "
        "Exceeding triggers fallback to direct search.",
    )

    def _resolve_collection(self) -> str:
        """Resolve collection name from attribute, config, or agent_id."""
        return (
            self.collection
            or (self.config.get("collection") if self.config else None)
            or getattr(self, "agent_id", None)
            or "default"
        )

    async def on_register(self) -> None:
        """Push ingestion config for document assimilation when action is registered."""
        await super().on_register()
        ingestion = _get_ingestion_config(self.config, self.node_summary)
        _push_ingestion_config(ingestion)

    async def on_reload(self) -> None:
        """Re-apply ingestion config when action is reloaded."""
        await super().on_reload()
        ingestion = _get_ingestion_config(self.config, self.node_summary)
        _push_ingestion_config(ingestion)

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute vectorless retrieval and add directive to interaction."""
        interaction = visitor.interaction
        if not interaction:
            logger.warning("PageIndexRetrievalInteractAction: No interaction")
            return

        ingestion = _get_ingestion_config(self.config, self.node_summary)
        _push_ingestion_config(ingestion)
        model_action = await self.get_model_action()
        prev_interaction = get_interaction()
        try:
            set_interaction(interaction)
            if model_action:
                set_pageindex_model_action(model_action)

            query = self._get_search_query(interaction)
            if not query:
                logger.debug("PageIndexRetrievalInteractAction: No query")
                return

            initialize_pageindex_database()
            # Config can override attributes (allows retrieval params in context or config)
            limit = self.config.get("limit", self.limit)
            strategy = self.config.get("strategy", self.strategy)
            model = self.config.get("model", self.model)
            doc_name = self.doc_name or self.config.get("doc_name")
            collection_name = self._resolve_collection()
            metadata_filter = self.metadata_filter or self.config.get("metadata_filter")
            max_summary_chars = self.config.get(
                "max_summary_chars", self.max_summary_chars
            )
            max_tree_prompt_tokens = self.config.get(
                "max_tree_prompt_tokens", self.max_tree_prompt_tokens
            )
            _push_retrieval_config(
                {
                    "max_summary_chars": max_summary_chars,
                    "max_tree_prompt_tokens": max_tree_prompt_tokens,
                }
            )
            results = await search_documents(
                query=query,
                doc_name=doc_name,
                strategy=strategy,
                limit=limit,
                model=model,
                collection_name=collection_name,
                metadata_filter=metadata_filter,
                max_summary_chars=max_summary_chars,
                max_tree_prompt_tokens=max_tree_prompt_tokens,
            )

            if results:
                directive = self._format_directive(results)
                await visitor.add_directive(directive)
                logger.debug(
                    f"PageIndexRetrievalInteractAction: Added directive with "
                    f"{len(results)} results"
                )
            if self.parameters:
                await visitor.add_parameters(self.parameters)

        except Exception as e:
            logger.error(
                f"PageIndexRetrievalInteractAction: Error: {e}",
                exc_info=True,
            )
        finally:
            set_pageindex_model_action(None)
            set_interaction(prev_interaction)

    def _get_search_query(self, interaction: "Interaction") -> Optional[str]:
        """Get search query from utterance or interpretation.

        Prefer utterance for retrieval—it contains the actual search terms. Interpretation
        is often a meta-description (e.g. "User is asking for information about X") that
        does not match document content.
        """
        query = interaction.utterance or interaction.interpretation
        return query.strip() if query else None

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format retrieval results into directive string."""
        parts = []
        for r in results:
            content = r.get("content", r.get("text", r.get("title", "")))
            title = r.get("title", "")
            doc = r.get("doc_name", "")
            prefix = f"[{doc}] {title}: " if doc or title else ""
            parts.append(f"- {prefix}{content}")
        return self.directive.format(results="\n".join(parts))
