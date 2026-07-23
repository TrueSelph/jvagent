"""PageIndexAction — graph Action for PageIndex search, ingest, document APIs.

Includes the inbound **jvforge LLM webhook** URL and completions
(``get_webhook_url``, ``handle_webhook_payload``, persisted webhook credentials).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Dict, List, Optional

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.exceptions import ValidationError
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url
from jvagent.env import get_jvagent_jvforge_base_url
from jvagent.tooling.tool_decorator import collect_tools, tool

from .. import llm_bridge
from ..core import utils as pageindex_core_utils
from ..documents import looks_like_doc_path
from ..prompts import (
    DIRECTIVE_TEMPLATE,
    DIRECTIVE_TEMPLATE_NO_REFS,
    DIRECTIVE_TEMPLATE_PLAIN,
)
from ..webhook_auth import (
    ALLOWED_WEBHOOK_ENDPOINT_GLOB,
    PAGEINDEX_WEBHOOK_ROUTE_PREFIX,
    WEBHOOK_PERMISSION,
    get_or_create_system_user,
)
from .runtime_config import format_page_range

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class PageIndexAction(Action):
    """Core PageIndex: ``search``, ``assimilate``, ``list_documents``, ``delete_document``."""

    # AUDIT-actions XC-4: admin-facing pageindex routes under
    # /agents/{agent_id}/pageindex/. ~18 routes; per-agent grouping.
    # The /pageindex_retrieval_interact_action/interact/webhook/{agent_id}
    # webhook also lives here for ingestion callbacks.
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/agents/{agent_id}/pageindex/",
        "/pageindex_retrieval_interact_action/interact/webhook/{agent_id}",
    ]

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
    metadata_filter: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Optional key-value filter to narrow search by document metadata (not for access control; use access_control instead)",
    )
    access_control: bool = attribute(
        default=False,
        description="When True, apply group-based access control to document retrieval. "
        "When False (default), all documents are accessible regardless of group membership. "
        "When enabled, access is always granted to 'public' documents; additional groups "
        "are resolved from AccessControlAction.user_groups based on visitor identity.",
    )
    directive: str = attribute(
        default=DIRECTIVE_TEMPLATE.template,
        description="Template for formatting the directive. Placeholders: {results}, {references}",
    )
    directive_no_refs: str = attribute(
        default=DIRECTIVE_TEMPLATE_NO_REFS.template,
        description="Template for formatting the directive without reference metadata. Placeholder: {results}",
    )
    directive_plain: str = attribute(
        default=DIRECTIVE_TEMPLATE_PLAIN.template,
        description="Template for formatting the directive in plain text. Placeholder: {results}",
    )

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

    async def _ensure_jvforge_llm_webhook_if_configured(self) -> None:
        """Provision inbound LLM webhook only when jvforge is configured (jvforge node-summary callback)."""
        if not (get_jvagent_jvforge_base_url() or "").strip():
            return
        await self.get_webhook_url()

    async def on_register(self) -> None:
        await super().on_register()
        from .runtime_config import get_ingestion_config, push_ingestion_config

        push_ingestion_config(get_ingestion_config(self.config, self.node_summary))
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self._ensure_jvforge_llm_webhook_if_configured()

    async def on_reload(self) -> None:
        await super().on_reload()
        from .runtime_config import get_ingestion_config, push_ingestion_config

        push_ingestion_config(get_ingestion_config(self.config, self.node_summary))
        from ..config import initialize_pageindex_database

        app = await self.get_app()
        app_id = getattr(app, "app_id", None) if app else None
        initialize_pageindex_database(app_id=app_id)
        await self._ensure_jvforge_llm_webhook_if_configured()

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
        visitor: Optional[InteractWalker] = None,
        access_control: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        from ..config import (
            initialize_pageindex_database,
            set_pageindex_candidate_k,
            set_pageindex_enable_lexical_index,
            set_pageindex_max_docs_for_tree_search,
            set_pageindex_max_summary_chars,
            set_pageindex_max_tree_prompt_tokens,
            set_pageindex_retrieval_excerpt_source,
        )
        from ..llm_bridge import (
            get_pageindex_model_action,
            set_pageindex_model_action,
        )
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
        resolved_access_control = (
            access_control if access_control is not None else self.access_control
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
                # Snapshot the live context var (not the static config dict —
                # which never carried this value) so concurrent / nested
                # search calls don't clobber an already-set parent action.
                prev_model_action = get_pageindex_model_action()
                set_pageindex_model_action(model_action)
            if resolved_access_control:
                # Always resolve access control even when visitor is None,
                # so visitors without identity default to public-only access
                # instead of bypassing access control entirely.
                metadata_filter = await self.resolved_metadata_filter(
                    visitor, metadata_filter, resolved_access_control
                )
            elif visitor is not None or self.metadata_filter is not None:
                # Non-AC path: resolve any action-level metadata filter
                metadata_filter = await self.resolved_metadata_filter(
                    visitor,
                    metadata_filter or self.metadata_filter,
                    resolved_access_control,
                )

            logger.debug(
                "PageIndex search: query=%r strategy=%s collection=%s "
                "metadata_filter=%s access_control=%s",
                query[:100] if query else "",
                resolved_strategy,
                resolved_collection,
                metadata_filter,
                resolved_access_control,
            )

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
        access_control: Optional[bool] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        summary: bool = False,
    ) -> List[Dict[str, Any]]:
        from ..documents import list_documents as _list_documents

        resolved_collection = collection_name or self._resolve_collection()
        resolved_ac = (
            access_control if access_control is not None else self.access_control
        )

        if not resolved_ac:
            result = await _list_documents(
                collection_name=resolved_collection,
                metadata_filter=metadata_filter,
            )
        else:
            import types

            visitor = types.SimpleNamespace(user_id=user_id, session_id=session_id)
            access_filter = await self.resolved_metadata_filter(
                visitor, metadata_filter=None, access_control=True
            )

            if metadata_filter and access_filter:
                combined = {**metadata_filter, **access_filter}
            elif access_filter:
                combined = access_filter
            else:
                combined = metadata_filter

            result = await _list_documents(
                collection_name=resolved_collection,
                metadata_filter=combined,
            )

        if summary:
            return [
                {
                    "doc_name": d["doc_name"],
                    "doc_description": d.get("doc_description", ""),
                }
                for d in result
            ]
        return result

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
        from ..documents import assimilate_document
        from ..llm_bridge import (
            get_pageindex_model_action,
            set_pageindex_model_action,
        )

        model_action = await self.get_model_action(required=False)
        prev_model_action = None
        try:
            if model_action:
                # Snapshot the live context var (see search() for rationale).
                prev_model_action = get_pageindex_model_action()
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

    async def get_tools(self) -> List[Any]:
        # Decorated methods supply names/descriptions/schemas; the assimilate
        # schema additionally pins ``additionalProperties: false``, which the
        # signature deriver does not emit, so we re-add it here.
        tools = collect_tools(self)
        for t in tools:
            if t.name == "pageindex__assimilate":
                t.parameters_schema["additionalProperties"] = False
                # ``doc`` is runtime-optional (the body coalesces aliases like
                # content/text/url), so it carries a signature default and the
                # deriver omits it from ``required``. The published contract
                # still presents it as required, so re-assert that here.
                t.parameters_schema["required"] = ["doc"]
        return tools

    # Aliases a model might use for doc_name instead of the canonical name.
    _name_aliases = ("name", "title", "doc_title", "filename", "file_name")

    @tool(name="pageindex__search")
    async def _t_search(
        self,
        query: Annotated[str, "Search query."],
        doc_name: Annotated[
            Optional[str], "Restrict search to a specific document name."
        ] = None,
        limit: Annotated[int, "Max results to return (default 5)."] = 5,
        **kwargs: Any,
    ) -> str:
        """Search the internal knowledge base for documents matching a query."""
        import json

        from jvagent.tooling.tool_executor import get_tool_visitor

        query = query or kwargs.get("q") or kwargs.get("text") or ""
        if not query:
            return json.dumps(
                {"error": "no query provided: pass it in 'query'"}, indent=2
            )
        visitor = get_tool_visitor()
        logger.debug(
            "PageIndex _t_search: visitor=%s user_id=%s session_id=%s access_control=%s",
            type(visitor).__name__ if visitor else "None",
            getattr(visitor, "user_id", None) if visitor else None,
            getattr(visitor, "session_id", None) if visitor else None,
            self.access_control,
        )
        results = await self.search(
            query,
            limit=limit,
            doc_name=doc_name,
            visitor=visitor,
            metadata_filter=self.metadata_filter,
        )
        if not results:
            return "No matching documents found."
        return json.dumps(results, indent=2)

    @tool(name="pageindex__assimilate")
    async def _t_assimilate(
        self,
        doc: Annotated[
            Optional[str],
            "REQUIRED. The document to ingest, as a single string. One of: "
            "(a) raw text/markdown content, (b) an http(s) URL to a web page or "
            "PDF (fetched automatically), or (c) a local file path. This is the "
            "only place to put the document — do not use 'source', 'content', "
            "'file', 'url', or 'path'.",
        ] = None,
        doc_name: Annotated[
            Optional[str],
            "Optional display name for the document (also used to infer the file "
            "type for raw content). Defaults to a name derived from a URL/path.",
        ] = None,
        **kwargs: Any,
    ) -> str:
        """Ingest one document into the knowledge base so it can be searched later with pageindex__search. Provide the document in the REQUIRED parameter named exactly `doc` (not 'source', 'content', 'file', or 'url'). The value of `doc` may be raw text/markdown, an http(s) URL (downloaded automatically — web pages and PDFs supported), or a local file path. Example call: {"doc": "https://example.com/report.pdf", "doc_name": "Q3 Report"}."""  # noqa: E501
        import json

        if not doc_name:
            for alias in self._name_aliases:
                if kwargs.get(alias):
                    doc_name = kwargs.pop(alias)
                    break
        # Coalesce the document argument. Lesser models reach for plausible
        # names (source / content / text / url / file / path / data ...);
        # accept the known aliases first, then fall back to ANY remaining
        # non-empty string kwarg — the tool only takes doc + doc_name, so a
        # stray string is the document. A near-miss arg name never errors.
        if not doc:
            for alias in (
                "source",
                "content",
                "text",
                "document",
                "doc_content",
                "url",
                "file",
                "path",
                "file_path",
                "data",
                "body",
                "input",
            ):
                if kwargs.get(alias):
                    doc = kwargs[alias]
                    break
        if not doc:
            for value in kwargs.values():
                if isinstance(value, str) and value.strip():
                    doc = value
                    break
        if not doc:
            return json.dumps(
                {
                    "error": "no document provided. Pass the document text, "
                    "an http(s) URL, or a file path as the 'doc' argument, "
                    'e.g. {"doc": "https://example.com/report.pdf"}.'
                },
                indent=2,
            )
        # If ``doc`` is a path/filename (e.g. a file just written by
        # code_execution__bash or file_interface__write_file), resolve it from
        # the caller's per-user sandbox and ingest the file CONTENT — not the
        # literal filename string. Raw text and URLs pass through untouched.
        if isinstance(doc, str) and looks_like_doc_path(doc):
            src_path = doc.strip()
            resolved, err = await self._resolve_sandbox_doc(src_path)
            if err:
                return json.dumps({"error": err}, indent=2)
            doc = resolved
            if not doc_name:
                doc_name = os.path.basename(src_path) or None
        result = await self.assimilate(doc, doc_name=doc_name or None)
        return json.dumps(result, indent=2)

    async def _resolve_sandbox_doc(self, rel_path: str):
        """Read a sandbox-relative document path into content/bytes.

        Returns ``(doc, None)`` on success (text str for text-like extensions,
        bytes otherwise) or ``(None, error_message)`` when the path cannot be
        read from the caller's sandbox. The sandbox slice is the same one
        ``code_execution__bash`` and ``file_interface__*`` use.
        """
        from jvagent.action.file_interface import _core
        from jvagent.tooling.tool_executor import get_tool_visitor

        from ..documents import DOC_TEXT_EXTENSIONS

        rel = rel_path.strip()
        visitor = get_tool_visitor()
        if visitor is None:
            return None, (
                f"{rel!r} looks like a file path but there is no execution "
                "context to resolve your workspace. Pass the document content "
                "directly as 'doc'."
            )
        ext = os.path.splitext(rel)[1].lower()
        try:
            if ext in DOC_TEXT_EXTENSIONS:
                content = await _core.read_text_file(visitor, rel)
                if not (content or "").strip():
                    return None, f"{rel!r} is empty — nothing to ingest."
                return content, None
            data = await _core.read_binary_file(visitor, rel)
            if not data:
                raise FileNotFoundError(rel)
            return data, None
        except Exception as exc:  # FileNotFoundError, sandbox errors, etc.
            return None, (
                f"{rel!r} looks like a file path but could not be read from "
                f"your workspace ({type(exc).__name__}). Pass the document "
                "content directly as 'doc', or a valid workspace-relative path "
                "(e.g. a file you wrote with code_execution__bash or "
                "file_interface__write_file)."
            )

    @tool(name="pageindex__list")
    async def _t_list_docs(
        self,
        collection_name: Annotated[
            Optional[str], "Optional collection to filter by."
        ] = None,
        summary: Annotated[
            bool,
            "If true, return only document names and descriptions (lighter response for quick lookup).",
        ] = False,
    ) -> str:
        """List documents in the knowledge base. When access control is enabled,
        only documents the current user can access are returned. Set summary=true
        to get document names and descriptions for quick lookup."""
        import json

        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        result = await self.list_documents(
            collection_name=collection_name or None,
            access_control=self.access_control,
            user_id=getattr(visitor, "user_id", None) if visitor else None,
            session_id=getattr(visitor, "session_id", None) if visitor else None,
            summary=summary,
        )
        return json.dumps(result, indent=2)

    @tool(name="pageindex__delete")
    async def _t_delete_doc(
        self,
        doc_name: Annotated[str, "Name of the document to delete."],
        collection_name: Annotated[Optional[str], "Optional collection name."] = None,
        **kwargs: Any,
    ) -> str:
        """Delete a document from the knowledge base by name."""
        doc_name = doc_name or kwargs.get("name") or kwargs.get("document") or ""
        if not doc_name:
            return "No document name provided (pass it in 'doc_name')."
        ok = await self.delete_document(
            doc_name, collection_name=collection_name or None
        )
        return f"Document '{doc_name}' {'deleted' if ok else 'not found'}."

    async def resolved_metadata_filter(
        self,
        visitor: Optional[InteractWalker],
        metadata_filter: Optional[Dict[str, Any]] = None,
        access_control: bool = False,
    ) -> Any:
        """Resolve effective metadata_filter, optionally applying access control.

        **Access control is opt-in via ``access_control``.** When ``access_control``
        is ``False`` (the default), no access filtering is applied — the
        metadata filter (if any) is returned unchanged. When ``True``, the
        agent's ``AccessControlAction.user_groups`` is consulted under the
        ``PageIndexAction`` scope and the visitor's matching group names are
        used to build a pure access filter.

        When ``access_control`` is ``True``:

        *   ``metadata_filter`` is **not** merged — access groups are the sole
            filter. This decouples access control from document metadata
            filtering so the two concerns do not interfere.
        *   ``"public"`` is **always** included in the ``access`` list, ensuring
            every visitor can reach public (untagged or ``access: "public"``)
            documents.
        *   If the visitor matches one or more groups (their ``user_id`` or
            ``session_id`` appears in the group's user list), those group names
            are added alongside ``"public"`` — e.g.
            ``access=["public", "private"]``.
        *   If the visitor matches **no** group, the access list is
            ``["public"]`` — only public/untagged documents are returned.
        *   If the visitor is ``None`` or has no identity (both ``user_id`` and
            ``session_id`` are ``None``), access defaults to ``["public"]``
            instead of bypassing access control entirely.

        Typical model: tag documents ``access: "public"`` or
        ``access: "private"``, set ``access_control: true`` on the action, and
        list the user ids allowed to see private docs under
        ``user_groups["PageIndexAction"]["private"]``. A member of
        ``private`` then sees public + private; everyone else sees public only.

        Document ``access`` tags should be **scalar** group names. The JSON/Mongo
        ``$in`` used by the DB layer cannot intersect a list-valued field, so
        list-valued per-document tags are only honored on the in-Python
        tree/walker paths, not the direct path. The *filter* side (the visitor's
        groups) is always a list and matches any document whose scalar ``access``
        is one of those groups. Matching lives in ``_build_metadata_query`` (DB
        layer) and ``_root_matches_metadata`` (in-Python layer).
        """
        logger.debug(
            "PageIndex access_control: enter resolved_metadata_filter "
            "access_control=%s metadata_filter=%s visitor.user_id=%s visitor.session_id=%s",
            access_control,
            metadata_filter,
            getattr(visitor, "user_id", None) if visitor else None,
            getattr(visitor, "session_id", None) if visitor else None,
        )

        if not access_control:
            logger.debug(
                "PageIndex access_control: access_control=False, "
                "returning metadata_filter unchanged"
            )
            return metadata_filter

        # Access control is on — build a pure access filter.
        # metadata_filter is intentionally NOT merged so that
        # access control and document metadata filtering stay decoupled.
        if visitor is None or (visitor.user_id is None and visitor.session_id is None):
            logger.debug(
                "PageIndex access_control: visitor has no identity "
                "(visitor=%s, user_id=%s, session_id=%s); "
                "returning access=['public'] only",
                type(visitor).__name__ if visitor else "None",
                visitor.user_id if visitor else None,
                visitor.session_id if visitor else None,
            )
            return {"access": ["public"]}

        access_control_action = await self.get_action(
            "AccessControlAction", enabled_only=False
        )
        if not access_control_action:
            logger.debug(
                "PageIndex access_control: AccessControlAction not found; "
                "returning access=['public'] only"
            )
            return {"access": ["public"]}

        if not access_control_action.user_groups:
            logger.debug(
                "PageIndex access_control: AccessControlAction has no user_groups; "
                "returning access=['public'] only"
            )
            return {"access": ["public"]}

        page_index_groups = access_control_action._resolve_user_groups(
            "PageIndexAction"
        )
        logger.debug(
            "PageIndex access_control: resolved user_groups for PageIndexAction: %s",
            page_index_groups,
        )

        if not page_index_groups:
            logger.debug(
                "PageIndex access_control: no groups resolved for PageIndexAction; "
                "returning access=['public'] only"
            )
            return {"access": ["public"]}

        matched_groups: List[str] = [
            group
            for group, users in page_index_groups.items()
            if (visitor.user_id is not None and visitor.user_id in users)
            or (visitor.session_id is not None and visitor.session_id in users)
        ]
        logger.debug(
            "PageIndex access_control: visitor.user_id=%r visitor.session_id=%r "
            "matched_groups=%s",
            visitor.user_id,
            visitor.session_id,
            matched_groups,
        )

        if not matched_groups:
            logger.debug(
                "PageIndex access_control: visitor (user_id=%r, session_id=%r) "
                "matched no groups; returning access=['public'] only",
                visitor.user_id,
                visitor.session_id,
            )
            return {"access": ["public"]}

        result = {"access": ["public", *matched_groups]}
        logger.debug(
            "PageIndex access_control: visitor (user_id=%r, session_id=%r) "
            "matched groups %s; returning access=%s",
            visitor.user_id,
            visitor.session_id,
            matched_groups,
            result["access"],
        )
        return result

    def _resolve_include_references(self) -> bool:
        cfg = self.config or {}
        if "include_references" in cfg and cfg["include_references"] is not None:
            from .runtime_config import bool_from_config

            return bool_from_config(cfg["include_references"], self.include_references)
        return self.include_references

    def format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format search results into a directive string with references.

        When ``include_references`` is True and results have reference metadata,
        produces numbered excerpts with a references block. When True but no
        reference metadata, produces numbered excerpts without a references
        block. When False, produces a plain bullet list.
        """
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
