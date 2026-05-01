"""PageIndexAction — graph Action for PageIndex search, ingest, document APIs.

Includes the inbound **jvforge LLM webhook** URL and completions
(``get_webhook_url``, ``handle_webhook_payload``, persisted webhook credentials).

Interact-time retrieval (directives, ``user_groups``) lives in
``PageIndexRetrievalInteractAction``, which calls this action's ``search`` method.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.exceptions import ValidationError
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .. import llm_bridge
from ..core import utils as pageindex_core_utils
from ..webhook_auth import (
    ALLOWED_WEBHOOK_ENDPOINT_GLOB,
    PAGEINDEX_WEBHOOK_ROUTE_PREFIX,
    WEBHOOK_PERMISSION,
    get_or_create_system_user,
)

logger = logging.getLogger(__name__)


class PageIndexAction(Action):
    """Core PageIndex: ``search``, ``assimilate``, ``list_documents``, ``delete_document``."""

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
    model: Optional[str] = attribute(
        default=None,
        description="LLM model id for tree_search (optional)",
    )
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction type for tree_search",
    )
    max_summary_chars: Optional[int] = attribute(
        default=None,
        description="Default max chars per node summary in tree prompt",
    )
    max_tree_prompt_tokens: Optional[int] = attribute(
        default=None,
        description="Default max tokens for tree in tree-search prompt",
    )
    retrieval_excerpt_source: str = attribute(
        default="summary",
        description="Tree prompt / excerpt mode: 'summary' or 'text'",
    )
    webhook_url: Optional[str] = attribute(
        default=None,
        description="Full inbound jvforge LLM webhook URL (includes api_key query when generated)",
    )
    webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="API key row id for LLM webhook auth",
    )

    async def _maybe_migrate_legacy_webhook_from_retrieval(self) -> None:
        """Copy webhook URL from PageIndexRetrievalInteractAction if still stored there."""
        if (self.webhook_url or "").strip() and "?api_key=" in (self.webhook_url or ""):
            return
        agent = await self.get_agent()
        retrieval = await agent.get_action_by_type("PageIndexRetrievalInteractAction")
        if not retrieval:
            return
        legacy_url = getattr(retrieval, "webhook_url", None) or ""
        if not legacy_url or "?api_key=" not in legacy_url:
            return
        agent_id = str(agent.id)
        base_url = (get_public_base_url() or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            return
        expected_url_base = (
            f"{base_url}/api/{PAGEINDEX_WEBHOOK_ROUTE_PREFIX}/{agent_id}"
        )
        if not legacy_url.startswith(expected_url_base):
            return
        legacy_key_id = getattr(retrieval, "webhook_api_key_id", None)
        self.webhook_url = legacy_url
        self.webhook_api_key_id = legacy_key_id
        await self.save()

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Public URL (+ api_key) jvforge uses for node-summary LLM completions."""
        base_url = (get_public_base_url() or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError(
                message="Set JVAGENT_PUBLIC_BASE_URL to a valid http(s) URL",
                details={"JVAGENT_PUBLIC_BASE_URL": base_url or "(empty)"},
            )

        try:
            await self._maybe_migrate_legacy_webhook_from_retrieval()
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = (
                f"{base_url}/api/{PAGEINDEX_WEBHOOK_ROUTE_PREFIX}/{agent_id}"
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
                permissions=[WEBHOOK_PERMISSION],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=[ALLOWED_WEBHOOK_ENDPOINT_GLOB],
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
        """LLM completion for jvforge (prompt + optional model)."""
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError(
                message="prompt is required",
                details={"prompt": payload.get("prompt")},
            )

        model = payload.get("model")
        if model is None or (isinstance(model, str) and not str(model).strip()):
            model = self.model or env(
                "PAGEINDEX_TREE_SEARCH_MODEL", default="gpt-4o-mini"
            )
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
        await super().on_register()
        from .runtime_config import get_ingestion_config, push_ingestion_config

        push_ingestion_config(get_ingestion_config(self.config, self.node_summary))
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self.get_webhook_url()

    async def on_reload(self) -> None:
        await super().on_reload()
        from .runtime_config import get_ingestion_config, push_ingestion_config

        push_ingestion_config(get_ingestion_config(self.config, self.node_summary))
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self.get_webhook_url()

    def resolve_collection(self) -> str:
        """Resolve the PageIndex collection name (public API for sibling actions)."""
        return self._resolve_collection()

    def _resolve_collection(self) -> str:
        cfg = self.config or {}
        return (
            self.collection
            or cfg.get("collection")
            or getattr(self, "agent_id", None)
            or "default"
        )

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
        model: Optional[str] = None,
        enable_lexical_index: Optional[Any] = None,
        candidate_k: Optional[Any] = None,
        max_docs_for_tree_search: Optional[Any] = None,
        retrieval_excerpt_source: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        from ..config import (
            get_pageindex_config,
            initialize_pageindex_database,
            set_pageindex_candidate_k,
            set_pageindex_enable_lexical_index,
            set_pageindex_max_docs_for_tree_search,
            set_pageindex_max_summary_chars,
            set_pageindex_max_tree_prompt_tokens,
            set_pageindex_retrieval_excerpt_source,
        )
        from ..llm_bridge import set_pageindex_model_action
        from ..retrieval import search_documents

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)

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

        eff_max_summary = (
            max_summary_chars
            if max_summary_chars is not None
            else (
                cfg["max_summary_chars"]
                if cfg.get("max_summary_chars") is not None
                else self.max_summary_chars
            )
        )
        eff_max_tree = (
            max_tree_prompt_tokens
            if max_tree_prompt_tokens is not None
            else (
                cfg["max_tree_prompt_tokens"]
                if cfg.get("max_tree_prompt_tokens") is not None
                else self.max_tree_prompt_tokens
            )
        )

        if eff_max_summary is not None:
            set_pageindex_max_summary_chars(eff_max_summary)
        if eff_max_tree is not None:
            set_pageindex_max_tree_prompt_tokens(eff_max_tree)

        eff_lex = (
            enable_lexical_index
            if enable_lexical_index is not None
            else cfg.get("enable_lexical_index")
        )
        if eff_lex is not None:
            set_pageindex_enable_lexical_index(eff_lex)

        eff_ck = candidate_k if candidate_k is not None else cfg.get("candidate_k")
        if eff_ck is not None:
            set_pageindex_candidate_k(eff_ck)

        eff_mdfs = (
            max_docs_for_tree_search
            if max_docs_for_tree_search is not None
            else cfg.get("max_docs_for_tree_search")
        )
        if eff_mdfs is not None:
            set_pageindex_max_docs_for_tree_search(eff_mdfs)

        eff_res = (
            retrieval_excerpt_source
            if retrieval_excerpt_source is not None
            else cfg.get("retrieval_excerpt_source")
        )
        if eff_res is not None:
            set_pageindex_retrieval_excerpt_source(eff_res)

        resolved_model = (
            model if model is not None else (cfg.get("model") or self.model)
        )

        model_action = await self.get_model_action(required=False)
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
                model=resolved_model,
                collection_name=resolved_collection,
                metadata_filter=metadata_filter,
                max_summary_chars=eff_max_summary,
                max_tree_prompt_tokens=eff_max_tree,
                include_references=resolved_include_refs,
                only_enabled=resolved_only_enabled,
                include=include,
            )
        finally:
            if model_action:
                set_pageindex_model_action(prev_model_action)

    async def list_documents(
        self,
        collection_name: Optional[str] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        from ..documents import list_documents as _list_documents

        return await _list_documents(
            collection_name=collection_name or self._resolve_collection(),
            metadata_filter=metadata_filter,
        )

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
        docling_ocr_engine: Optional[str] = None,
    ) -> Dict[str, Any]:
        from ..config import get_pageindex_config
        from ..documents import assimilate_document
        from ..llm_bridge import set_pageindex_model_action

        model_action = await self.get_model_action(required=False)
        prev_model_action = None
        try:
            if model_action:
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
                docling_ocr_engine=docling_ocr_engine,
            )
        finally:
            if model_action:
                set_pageindex_model_action(prev_model_action)

    async def delete_document(
        self,
        doc_name: str,
        *,
        collection_name: Optional[str] = None,
    ) -> bool:
        from ..documents import delete_document as _delete_document

        return await _delete_document(
            doc_name=doc_name,
            collection_name=collection_name or self._resolve_collection(),
        )
