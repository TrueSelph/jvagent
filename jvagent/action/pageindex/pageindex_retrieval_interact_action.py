"""PageIndexRetrievalInteractAction for vectorless RAG.

Uses PageIndex graph with LLM-based tree search (default) or text filtering.
No embeddings, no vector store.
"""

import copy
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.exceptions import ValidationError
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.context import get_interaction, set_interaction
from jvagent.core.public_url import get_public_base_url
from jvagent.action.pageindex.config import (
    initialize_pageindex_database,
    set_pageindex_candidate_k,
    set_pageindex_doc_description,
    set_pageindex_enable_lexical_index,
    set_pageindex_max_docs_for_tree_search,
    set_pageindex_max_summary_chars,
    set_pageindex_max_token_num_each_node,
    set_pageindex_max_tree_prompt_tokens,
    set_pageindex_node_summary,
    set_pageindex_node_text,
    set_pageindex_retrieval_excerpt_source,
    set_pageindex_summary_token_threshold,
)
from jvagent.action.pageindex.prompts import (
    DIRECTIVE_TEMPLATE,
    DIRECTIVE_TEMPLATE_NO_REFS,
    DIRECTIVE_TEMPLATE_PLAIN,
    DIRECTIVE_TEMPLATE_STR,
)
from jvagent.action.pageindex.retrieval import search_documents

from . import llm_bridge
from .core import utils as pageindex_core_utils
from .webhook_auth import get_or_create_system_user

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
    if "enable_lexical_index" in retrieval:
        set_pageindex_enable_lexical_index(retrieval["enable_lexical_index"])
    if "candidate_k" in retrieval and retrieval["candidate_k"] is not None:
        set_pageindex_candidate_k(retrieval["candidate_k"])
    if (
        "max_docs_for_tree_search" in retrieval
        and retrieval["max_docs_for_tree_search"] is not None
    ):
        set_pageindex_max_docs_for_tree_search(retrieval["max_docs_for_tree_search"])
    if (
        "retrieval_excerpt_source" in retrieval
        and retrieval["retrieval_excerpt_source"] is not None
    ):
        set_pageindex_retrieval_excerpt_source(
            str(retrieval["retrieval_excerpt_source"])
        )


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


def _normalize_retrieval_excerpt_source(value: Any, fallback: str) -> str:
    """Return 'text' or 'summary' for tree prompt and directive excerpts."""
    if value is None:
        v = str(fallback).lower().strip()
    else:
        v = str(value).lower().strip()
    return "text" if v == "text" else "summary"


def _format_page_range(r: Dict[str, Any]) -> str:
    """Format page range from result dict, e.g. 'pp. 5-8' or 'p. 5'."""
    start = r.get("start_index")
    end = r.get("end_index")
    if start is not None and end is not None and start != end:
        return f"pp. {start}-{end}"
    if start is not None:
        return f"p. {start}"
    return ""


async def ensure_ingestion_config_for_agent(agent_id: str) -> None:
    """Push ingestion config from agent's PageIndex action to config module.

    Used when REST ingest does not receive if_add_node_summary in the form.
    Resolves config from cached actions; falls back to text-first ingestion when
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
    # Fallback: text-first tree excerpts; LLM summaries off unless config enables them
    _push_ingestion_config(
        {
            "node_summary": False,
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
        default=DIRECTIVE_TEMPLATE_STR,
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
        description="When True, generate LLM node summaries during ingestion (if_add_node_summary='yes'). "
        "Recommended when using retrieval_excerpt_source summary (default).",
    )
    retrieval_excerpt_source: str = attribute(
        default="summary",
        description="What to show in tree_search prompts and directive excerpts: 'summary' "
        "(prefer stored summaries, else body text) or 'text' (prefer full section text, else summaries).",
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
    include_references: bool = attribute(
        default=True,
        description="When True, render numbered source references with page numbers and "
        "document URLs in the directive. Set False to disable and save tokens.",
    )
    only_enabled: bool = attribute(
        default=True,
        description="When True, retrieval skips DocumentNodes with enabled=false.",
    )
    retrieval_include: Optional[List[str]] = attribute(
        default=None,
        description="Optional extra fields per hit (e.g. hierarchy, content_type, "
        "pageindex_node_id). Same as REST search body `include`.",
    )
    user_groups: Dict[str, List[str]] = attribute(
        default_factory=dict,
        description="Add user groups to use document filter. group to user ids.",
    )
    webhook_url: Optional[str] = attribute(
        default=None,
        description="Full inbound LLM webhook URL (includes api_key query when generated)",
    )
    webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="API key row id for LLM webhook auth",
    )

    def _resolve_collection(self) -> str:
        """Resolve collection name from attribute, config, or agent_id."""
        return (
            self.collection
            or (self.config.get("collection") if self.config else None)
            or getattr(self, "agent_id", None)
            or "default"
        )

    def _resolved_metadata_filter(self, visitor: "InteractWalker") -> Any:
        """Merge config/attribute metadata_filter with optional user_groups access rules."""
        cfg = self.config or {}
        base = self.metadata_filter or cfg.get("metadata_filter")
        if not self.user_groups:
            return base
        mf = copy.deepcopy(base) if base is not None else {}
        for group, users in self.user_groups.items():
            if visitor.user_id in users or visitor.session_id in users:
                if isinstance(mf, dict) and "access" in mf:
                    if isinstance(mf["access"], list):
                        mf["access"].append(group)
                    else:
                        mf["access"] = [mf["access"], group]
                else:
                    mf = {"access": [group]}
        return mf if mf else base

    def _retrieval_runtime_config(self, visitor: "InteractWalker") -> Dict[str, Any]:
        """Resolved limits, strategy, filters, and retrieval excerpt mode for one execute."""
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
            _bool_from_config(cfg["only_enabled"], self.only_enabled)
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
            "metadata_filter": self._resolved_metadata_filter(visitor),
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

    def _apply_ingestion_config(self) -> None:
        """Push ingestion config values from this action to the config module."""
        _push_ingestion_config(_get_ingestion_config(self.config, self.node_summary))

    def _resolve_retrieval_excerpt_source(self) -> str:
        cfg = self.config or {}
        if (
            "retrieval_excerpt_source" in cfg
            and cfg["retrieval_excerpt_source"] is not None
        ):
            return _normalize_retrieval_excerpt_source(
                cfg["retrieval_excerpt_source"], "summary"
            )
        return _normalize_retrieval_excerpt_source(
            self.retrieval_excerpt_source, "summary"
        )

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Generate or return the inbound LLM webhook URL (API key in query string)."""
        base_url = (get_public_base_url() or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError(
                message="Set JVAGENT_PUBLIC_BASE_URL to a valid http(s) URL",
                details={"JVAGENT_PUBLIC_BASE_URL": base_url or "(empty)"},
            )

        try:
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = (
                f"{base_url}/api/pageindex_retrieval_interact_action/interact/webhook/"
                f"{agent_id}"
            )

            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                if allowed_ip is not None and self.webhook_api_key_id:
                    try:
                        existing_key = await api_key_service.get_key(
                            self.webhook_api_key_id
                        )
                        if existing_key and existing_key.is_active:
                            requested_ips = [allowed_ip] if allowed_ip else []
                            existing_ips = (
                                getattr(existing_key, "allowed_ips", None) or []
                            )
                            if requested_ips == existing_ips:
                                return self.webhook_url
                    except Exception:
                        pass
                else:
                    return self.webhook_url

            system_user_id = await get_or_create_system_user()

            if regenerate and self.webhook_api_key_id:
                try:
                    await api_key_service.revoke_key(
                        self.webhook_api_key_id, system_user_id
                    )
                except Exception:
                    pass

            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"PageIndex LLM webhook — {agent.name}",
                permissions=["webhook:pageindex_retrieval_interact_action"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=[
                    "/api/pageindex_retrieval_interact_action/interact/webhook/*"
                ],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            await self.save()
            return self.webhook_url

        except DatabaseError:
            raise
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                message=f"Webhook URL generation failed: {e}",
                details={},
            )

    async def handle_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single LLM completion using this action's model action (PageIndex bridge).

        Body JSON:
            - ``prompt`` (str, required): user message / prompt text.
            - ``model`` (str, optional): model id; else action ``model`` or
              ``PAGEINDEX_TREE_SEARCH_MODEL`` (default ``gpt-4o-mini``).
        """
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError(
                message="prompt is required",
                details={"prompt": payload.get("prompt")},
            )

        model = payload.get("model")
        if model is None or (isinstance(model, str) and not str(model).strip()):
            model = self.model or env("PAGEINDEX_TREE_SEARCH_MODEL", default="gpt-4o-mini")
        else:
            model = str(model).strip()

        model_action = await self.get_model_action(required=False)
        try:
            llm_bridge.set_pageindex_model_action(model_action)
            text = await llm_bridge.llm_acompletion(
                model,
                prompt,
                _real_impl=pageindex_core_utils.llm_acompletion,
            )
        finally:
            llm_bridge.set_pageindex_model_action(None)

        return {"text": text or "", "model": model}

    async def on_register(self) -> None:
        """Push ingestion config and initialize PageIndex db when action is registered."""
        await super().on_register()
        self._apply_ingestion_config()
        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self.get_webhook_url()

    async def on_reload(self) -> None:
        """Re-apply ingestion config and re-init PageIndex db when action is reloaded."""
        await super().on_reload()
        self._apply_ingestion_config()
        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self.get_webhook_url()

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
                llm_bridge.set_pageindex_model_action(model_action)

            query = self._get_search_query(interaction)
            if not query:
                logger.debug("PageIndexRetrievalInteractAction: No query")
                return

            initialize_pageindex_database()
            rtc = self._retrieval_runtime_config(visitor)
            _push_retrieval_config(
                {
                    "max_summary_chars": rtc["max_summary_chars"],
                    "max_tree_prompt_tokens": rtc["max_tree_prompt_tokens"],
                    "enable_lexical_index": rtc["enable_lexical_index"],
                    "candidate_k": rtc["candidate_k"],
                    "max_docs_for_tree_search": rtc["max_docs_for_tree_search"],
                    "retrieval_excerpt_source": rtc["retrieval_excerpt_source"],
                }
            )
            results = await search_documents(
                query=query,
                doc_name=rtc["doc_name"],
                strategy=rtc["strategy"],
                limit=rtc["limit"],
                model=rtc["model"],
                collection_name=rtc["collection_name"],
                metadata_filter=rtc["metadata_filter"],
                max_summary_chars=rtc["max_summary_chars"],
                max_tree_prompt_tokens=rtc["max_tree_prompt_tokens"],
                include_references=self._resolve_include_references(),
                only_enabled=rtc["only_enabled"],
                include=rtc["include"],
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
            llm_bridge.set_pageindex_model_action(None)
            set_interaction(prev_interaction)

    def _get_search_query(self, interaction: "Interaction") -> Optional[str]:
        """Get search query from utterance or interpretation.

        Prefer utterance for retrieval—it contains the actual search terms. Interpretation
        is often a meta-description (e.g. "User is asking for information about X") that
        does not match document content.
        """
        query = interaction.utterance or interaction.interpretation
        return query.strip() if query else None

    def _resolve_include_references(self) -> bool:
        """Resolve include_references from config with attribute fallback."""
        if self.config and "include_references" in self.config:
            return _bool_from_config(self.config["include_references"], True)
        return self.include_references

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format retrieval results into directive string.

        When include_references is True, renders numbered excerpts with a
        deduplicated references section containing page ranges and URLs.
        When False, uses the plain flat format to save tokens.
        """
        if not self._resolve_include_references():
            return self._format_directive_plain(results)
        return self._format_directive_with_references(results)

    def _format_directive_plain(self, results: List[Dict[str, Any]]) -> str:
        """Original flat format without reference metadata."""
        parts = []
        for r in results:
            content = r.get("content", r.get("text", r.get("title", "")))
            title = r.get("title", "")
            doc = r.get("doc_name", "")
            prefix = f"[{doc}] {title}: " if doc or title else ""
            parts.append(f"- {prefix}{content}")
        return DIRECTIVE_TEMPLATE_PLAIN.safe_substitute(results="\n".join(parts))

    def _format_directive_with_references(self, results: List[Dict[str, Any]]) -> str:
        """Numbered excerpts with deduplicated references.

        Multiple excerpts from the same source share a single reference number,
        so the references block never contains duplicates.
        """
        source_to_ref: Dict[tuple, int] = {}
        ref_entries: List[str] = []
        has_ref_metadata = False

        for r in results:
            page_range = _format_page_range(r)
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
            page_range = _format_page_range(r)
            url = r.get("doc_url")
            ref_key = (doc or "", page_range or "", url or "")
            ref_num = source_to_ref[ref_key]
            label = f"[{doc}] {title}" if doc or title else "Excerpt"
            excerpt_lines.append(f"[{ref_num}] {label}: {content}")

        results_str = "\n".join(excerpt_lines)
        if has_ref_metadata and ref_entries:
            return DIRECTIVE_TEMPLATE.safe_substitute(
                results=results_str, references="\n".join(ref_entries)
            )
        return DIRECTIVE_TEMPLATE_NO_REFS.safe_substitute(results=results_str)
