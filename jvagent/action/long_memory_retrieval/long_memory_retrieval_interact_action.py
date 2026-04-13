"""UserLongMemoryRetrievalInteractAction for vectorless RAG.

Uses the assimilated user_long_memory_{user_id} document in PageIndex
with LLM-based tree search (default) or text filtering.
No embeddings, no vector store.
"""

import inspect
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.context import get_interaction, set_interaction
from jvagent.action.pageindex.config import (
    initialize_pageindex_database,
    set_pageindex_doc_description,
    set_pageindex_max_token_num_each_node,
    set_pageindex_node_summary,
    set_pageindex_node_text,
    set_pageindex_summary_token_threshold,
)
from jvagent.action.pageindex.llm_bridge import set_pageindex_model_action
from jvagent.action.pageindex.pageindex_retrieval_interact_action import (
    PageIndexRetrievalInteractAction,
    _push_retrieval_config,
)
from jvagent.action.pageindex.retrieval import search_documents
from jvagent.memory.long_memory_retrieval_utils import resolve_long_memory_collection
from jvagent.memory.user import User
from jvagent.memory.user_long_memory import UserLongMemory

from .prompts import DIRECTIVE_TEMPLATE

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


class UserLongMemoryRetrievalInteractAction(PageIndexRetrievalInteractAction):
    """InteractAction that retrieves context from the user's profile collection in PageIndex.

    Searches the long memory collection (user_long_memory_{user_id}) via LLM-based tree search,
    direct, or walker strategies. No VectorStore, no embeddings.
    1. Uses interaction's utterance (or interpretation) as search query
    2. Searches the user's profile collection in PageIndex via tree_search, direct, or walker
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
    history_limit: int = attribute(
        default=3,
        description="Number of recent history to retrieve",
        ge=1,
    )
    weight: int = attribute(
        default=-75,
        description="Execution weight (runs after InteractRouter)",
    )
    always_execute: bool = attribute(
        default=False,
        description="Disabled autonomous decision making as InteractRouter handles this via anchors",
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
        default_factory=lambda: [
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
        default="LongTermMemory",
        description="Collection name (default: agent_id). Override via context or config.",
    )
    metadata_filter: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Optional key-value filter to narrow search by document metadata. "
        "Supports dynamic placeholders: {user_id}, {session_id}, {agent_id}, or any interaction attribute. "
        "Example: {'user_id': '{user_id}', 'type': 'user_long_memory'} filters to documents with matching user_id and type (same as store metadata).",
    )
    point_of_interest: Optional[str] = attribute(
        default=None,
        description="A specific topic or category this action should focus on for retrieval.",
    )

    async def on_register(self) -> None:
        """Register the action with the agent."""
        await super().on_register()
        # Static fallback anchors (used when user context is unavailable, e.g. at startup)
        self.anchors.extend(
            [
                "user interests",
                "user preferences",
                "facts about the user",
                "user profile information",
                "long-term memory context",
            ]
        )
        if self.point_of_interest:
            self.anchors.append(f"needs context from {str(self.point_of_interest)}")

    async def get_anchors(self, conversation=None) -> list:
        """Return live anchors derived from the user's memory categories.

        Called by InteractRouter at routing time. Fetches category titles + keywords
        from UserLongMemory so the LLM knows what data is stored and can route here
        dynamically — even for categories added after deployment.

        Falls back to static self.anchors when:
        - No conversation or user is available
        - UserLongMemory has no categories or all are empty

        Args:
            conversation: Current Conversation node (may be None).

        Returns:
            A list of anchor strings (always non-empty: falls back to static anchors).
        """
        try:
            if conversation is None:
                return self.anchors  # type: ignore[return-value]

            graph_user = await conversation.node(direction="in", node=User)
            if not graph_user:
                return self.anchors  # type: ignore[return-value]

            user_long_memory = await UserLongMemory.get_for_user(graph_user)
            if not user_long_memory:
                return self.anchors  # type: ignore[return-value]

            categories = await user_long_memory.get_all_categories()
            dynamic: list = []
            for cat in categories:
                if cat.is_empty():
                    continue
                title = getattr(cat, "title", "") or ""
                keywords = list(getattr(cat, "keywords", []) or [])
                if title:
                    dynamic.append(f"user has stored: {title}")
                for kw in keywords:
                    if kw:
                        dynamic.append(str(kw))

            if dynamic:
                # Merge with static anchors (dedup, static first)
                seen = set(self.anchors)
                extra = [a for a in dynamic if a not in seen]
                logger.debug(
                    f"UserLongMemoryRetrieval.get_anchors: {len(extra)} dynamic anchors from memory"
                )
                return list(self.anchors) + extra  # type: ignore[return-value]
        except Exception as exc:
            logger.warning(f"UserLongMemoryRetrieval.get_anchors failed: {exc}")

        return self.anchors  # type: ignore[return-value]

    def _resolve_collection(self) -> str:
        """Resolve PageIndex collection as {agent_id}_{suffix}."""
        return resolve_long_memory_collection(
            getattr(self, "agent_id", None),
            getattr(self, "collection", None),
            getattr(self, "config", None),
        )

    @staticmethod
    async def _evaluate_search_need(
        act: Any,
        visitor: Any,
        model_action: Any,
    ) -> Dict[str, Any]:
        """Decide whether to SEARCH user long memory or CONTINUE without retrieval.

        Used by tests and optional callers. Flow:

        1. Keyword fast path: utterance (or interpretation) contains any keyword from a
           non-empty ``UserLongMemory`` category (case-insensitive substring) → SEARCH
           with query = utterance/interpretation text (or the keyword if empty).
        2. If no model action → CONTINUE.
        3. Otherwise call ``model_action.generate`` with recent history context; on
           failure → CONTINUE. On success, parse JSON or ``DECISION:`` line for
           SEARCH vs CONTINUE; default CONTINUE.
        """
        inter = getattr(visitor, "interaction", None)
        if not inter:
            return {"decision": "CONTINUE"}

        raw_text = (
            getattr(inter, "utterance", None)
            or getattr(inter, "interpretation", None)
            or ""
        )
        text = str(raw_text).strip()
        text_lower = text.lower()

        get_user = getattr(inter, "get_user", None)
        if not callable(get_user):
            return {"decision": "CONTINUE"}
        user = await get_user()
        if not user:
            return {"decision": "CONTINUE"}

        ulm = await UserLongMemory.get_for_user(user)
        if not ulm:
            return {"decision": "CONTINUE"}

        categories = await ulm.get_all_categories()
        for cat in categories or []:
            is_empty_fn = getattr(cat, "is_empty", None)
            if callable(is_empty_fn) and is_empty_fn():
                continue
            for kw in getattr(cat, "keywords", []) or []:
                if not kw:
                    continue
                if str(kw).lower() in text_lower:
                    return {"decision": "SEARCH", "query": text or str(kw)}

        if model_action is None:
            return {"decision": "CONTINUE"}

        try:
            history_ctx = ""
            gh = getattr(act, "_get_recent_history", None)
            if callable(gh):
                h = gh(visitor)
                if inspect.isawaitable(h):
                    history_ctx = await h
                else:
                    history_ctx = h
            prompt = (
                'Reply with JSON only: {"decision":"SEARCH"|"CONTINUE",'
                '"query":"<optional>"}.\n'
                f"User message: {text!r}\nRecent context: {history_ctx!r}\n"
                "SEARCH if user long-term profile/memory is needed to answer well."
            )
            raw = await model_action.generate(prompt=prompt)
        except Exception:
            logger.debug(
                "UserLongMemoryRetrieval: _evaluate_search_need LLM failed",
                exc_info=True,
            )
            return {"decision": "CONTINUE"}

        decision, query = UserLongMemoryRetrievalInteractAction._parse_search_decision(
            raw
        )
        if decision == "SEARCH":
            out: Dict[str, Any] = {"decision": "SEARCH"}
            if query:
                out["query"] = query
            elif text:
                out["query"] = text
            return out
        return {"decision": "CONTINUE"}

    @staticmethod
    def _parse_search_decision(raw: Any) -> Tuple[str, Optional[str]]:
        """Parse LLM output into (decision, optional query). decision is SEARCH or CONTINUE."""
        if raw is None:
            return "CONTINUE", None
        s = str(raw).strip()
        if not s:
            return "CONTINUE", None

        try:
            data = json.loads(s)
            if isinstance(data, dict):
                d = str(data.get("decision", "")).upper()
                if d == "SEARCH":
                    q = data.get("query")
                    return "SEARCH", str(q).strip() if q else None
                return "CONTINUE", None
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        m = re.search(
            r"^\s*DECISION:\s*(SEARCH|CONTINUE)\s*$",
            s,
            re.IGNORECASE | re.MULTILINE,
        )
        if m:
            return m.group(1).upper(), None

        if re.search(r"\bSEARCH\b", s, re.IGNORECASE):
            return "SEARCH", None
        return "CONTINUE", None

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

            # If SEARCH, use the query from LLM if provided, or fallback to default
            query = self._get_search_query(interaction)
            if not query:
                logger.debug("UserLongMemoryRetrieval: No query available for SEARCH")
                return

            initialize_pageindex_database()
            user_id = visitor.user_id
            # Config can override attributes (allows retrieval params in context or config)
            limit = self.config.get("limit", self.limit)
            strategy = self.config.get("strategy", self.strategy)
            model = self.config.get("model", self.model)
            doc_name = f"user_long_memory_{user_id}"
            collection_name = self._resolve_collection()
            metadata_filter = (
                self.metadata_filter or self.config.get("metadata_filter") or {}
            )
            if user_id:
                metadata_filter = {**metadata_filter, "user_id": str(user_id)}
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
        """Get search query from utterance or interpretation."""
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
