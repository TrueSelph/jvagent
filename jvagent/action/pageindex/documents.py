"""PageIndex document operations.

Wraps vendored PageIndex core (PDF ``page_index``) and enriched Markdown
(``md_tree_enriched.md_to_tree``) for assimilation, persisting structure to jvspatial.
"""

import asyncio
import functools
import logging
import os
import tempfile
import threading
from collections import Counter
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.core.context import (
    GraphContext,
    scoped_default_context_async,
)
from jvspatial.db import get_database_manager

from .adapter import _count_structure_nodes, strip_redundant_md_suffix, tree_to_graph
from .config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_doc_description,
    get_pageindex_max_token_num_each_node,
    get_pageindex_node_summary,
    get_pageindex_node_text,
    get_pageindex_summary_token_threshold,
    initialize_pageindex_database,
    resolve_pageindex_json_log_dir,
    resolve_pageindex_work_dir,
)
from .core import page_index
from .docling_convert import (
    convert_document_to_markdown_sync,
    wants_ooxml_pdf_for_docling_ocr,
)
from .llm_bridge import (
    PageIndexCancelled,
    attach_pageindex_cancel_event,
    set_pageindex_model_action,
    signal_pageindex_cancel,
)
from .markdown_pages import (
    annotate_markdown_structure_pages,
    strip_page_markers_and_build_line_page_map,
)
from .md_tree_enriched import (
    annotate_content_type_and_enabled,
    assign_hierarchy_breadcrumbs,
    md_to_tree,
)
from .models import DocumentContentEdge, DocumentNode, DocumentRootNode, node_enabled

logger = logging.getLogger(__name__)

# Max chunks returned in one list response (when per_page is 0 = "all", or cap page size).
CHUNK_LIST_MAX = 5000

# Extensions routed through Docling (binary office formats).
PAGEINDEX_OFFICE_LIKE_EXTENSIONS = frozenset(
    {".docx", ".doc", ".xls", ".xlsx", ".ppt", ".pptx"}
)
# UTF-8 text sources ingested as markdown-enriched (no Docling).
PAGEINDEX_TEXT_LIKE_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})
# Aligned with jvforge ``pi_vendor.docling_convert.DOCLING_IMAGE_EXTENSIONS``.
PAGEINDEX_DOCLING_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
)

# Aligned with jvforge ``multipart_ingest.ALLOWED_EXTENSIONS`` (upload / jvforge POST).
PAGEINDEX_UPLOAD_EXTENSIONS = frozenset(
    {".pdf"}
    | PAGEINDEX_TEXT_LIKE_EXTENSIONS
    | PAGEINDEX_OFFICE_LIKE_EXTENSIONS
    | PAGEINDEX_DOCLING_IMAGE_EXTENSIONS
)

# Extra UTF-8 text extensions accepted as content when a `doc` path is resolved.
_EXTRA_TEXT_EXTENSIONS = frozenset(
    {".html", ".htm", ".rst", ".json", ".csv", ".text", ".log", ".yaml", ".yml"}
)
# Every extension we recognise as "this looks like a document file".
KNOWN_DOC_EXTENSIONS = PAGEINDEX_UPLOAD_EXTENSIONS | _EXTRA_TEXT_EXTENSIONS
# Of those, the ones safe to read as UTF-8 text (vs. binary office/pdf/images).
DOC_TEXT_EXTENSIONS = PAGEINDEX_TEXT_LIKE_EXTENSIONS | _EXTRA_TEXT_EXTENSIONS


def looks_like_doc_path(value: Any) -> bool:
    """True when *value* reads as a file path/filename rather than document content.

    Used to (a) resolve a sandbox-relative file in the ``pageindex__assimilate``
    tool and (b) fail loud instead of silently ingesting a stray filename as the
    document body. Heuristic: short, single-line, not a URL, and either contains a
    path separator or ends in a known document extension. Genuine content (prose,
    multi-line markdown, long strings) does not match.
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or "\n" in s or len(s) >= 256:
        return False
    if s.startswith(("http://", "https://")):
        return False
    if "/" in s or os.sep in s or (os.altsep and os.altsep in s):
        return True
    return os.path.splitext(s)[1].lower() in KNOWN_DOC_EXTENSIONS


async def _ensure_pageindex_work_dir() -> str:
    """Resolved ``.../pageindex/tmp`` under App file_storage; created if missing."""
    from jvagent.core.app import App

    merged: Optional[str] = None
    try:
        app = await App.get()
        merged = app.file_storage_root_dir if app else None
    except RuntimeError:
        # No default GraphContext (e.g. unit tests without Server / App node).
        merged = None
    work_dir = resolve_pageindex_work_dir(merged)
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


async def _get_app_id_from_node() -> Optional[str]:
    """Get app_id from App node. JVAGENT_APP_ID env overrides when set in config."""
    try:
        from jvagent.core.app import App

        app = await App.get()
        return getattr(app, "app_id", None) if app else None
    except RuntimeError:
        # No default GraphContext (e.g. unit tests, scripts without Server startup)
        return None


def _scoped_pageindex_context(context: GraphContext):
    """Async context manager binding ``context`` as the per-task default.

    Wraps ``scoped_default_context_async`` so PageIndex operations can swap
    in the PageIndex graph DB for the duration of the call without leaking
    across concurrent requests. Replaces the prior manual prev/restore
    pattern that depended on a process-global mutation.
    """
    return scoped_default_context_async(context)


def _to_yes_no(value: Any, default: bool) -> str:
    """Normalize bool-like value to yes/no. None -> default; yes/true/1 -> yes; else no."""
    if value is None:
        return "yes" if default else "no"
    v = str(value).lower().strip()
    return "yes" if v in ("yes", "true", "1") else "no"


def enrich_structure_titles(structure: Any) -> Any:
    """Prefix node titles with hierarchy ``structure`` (e.g. 1.2.3) when missing."""
    if isinstance(structure, list):
        return [enrich_structure_titles(item) for item in structure]
    if not isinstance(structure, dict):
        return structure
    out = dict(structure)
    struct_code = str(out.get("structure") or "").strip()
    title = (out.get("title") or "").strip()
    if struct_code and struct_code != "0" and title:
        prefixed = (
            title.startswith(f"{struct_code} ")
            or title.startswith(f"{struct_code}.")
            or title == struct_code
        )
        if not prefixed:
            out["title"] = f"{struct_code} {title}".strip()
    nodes = out.get("nodes")
    if nodes:
        out["nodes"] = enrich_structure_titles(nodes)
    return out


def _build_metadata_query(metadata_filter: Dict[str, Any]) -> Dict[str, Any]:
    """Build query dict for metadata filter.

    Supports single-key, multi-key, and list-valued filters (OR semantics for lists).
    Uses dot notation for all keys to allow matching a subset of metadata.

    The reserved ``access`` key carries access-control semantics: a document
    with no ``access`` metadata (or an empty one) is public and matches any
    visitor, while a tagged document matches only when its ``access`` value
    intersects the visitor's allowed groups. An empty allowed-groups list thus
    scopes to public documents only, rather than matching nothing.
    """
    if not metadata_filter:
        return {}

    query: Dict[str, Any] = {}
    access_clause: Optional[Dict[str, Any]] = None
    for k, v in metadata_filter.items():
        if k == "access":
            groups = v if isinstance(v, list) else [v]
            field = "context.metadata.access"
            options: List[Dict[str, Any]] = [
                {field: {"$exists": False}},
                {field: None},
                {field: []},
            ]
            if groups:
                options.append({field: {"$in": groups}})
            access_clause = {"$or": options}
            logger.debug(
                "PageIndex _build_metadata_query: access key detected groups=%s → %s",
                groups,
                access_clause,
            )
            continue
        if isinstance(v, list):
            query[f"context.metadata.{k}"] = {"$in": v}
        else:
            query[f"context.metadata.{k}"] = v

    if access_clause is not None:
        result = {"$and": [query, access_clause]} if query else access_clause
    else:
        result = query
    logger.debug(
        "PageIndex _build_metadata_query: input=%s → output=%s",
        metadata_filter,
        result,
    )
    return result


def _get_pageindex_context() -> GraphContext:
    """Get GraphContext for the PageIndex database."""
    manager = get_database_manager()
    db = manager.get_database(PAGEINDEX_DB_NAME)
    return GraphContext(database=db)


def _pdf_page_index_worker(
    cancel_event: Optional[threading.Event],
    doc: Any,
    *,
    log_base: Optional[str],
    model: str,
    toc_check_page_num: Optional[int],
    max_page_num_each_node: Optional[int],
    max_token_num_each_node: Optional[int],
    if_add_node_id: str,
    if_add_node_text: str,
    if_add_node_summary: str,
    if_add_doc_description: str,
) -> Dict[str, Any]:
    """Run sync page_index in a thread with optional cooperative cancel (see llm_bridge).

    When ``log_base`` is set, the process cwd is temporarily changed so vendored
    PageIndex writes trace JSON under ``{log_base}/logs/`` (upstream uses a
    relative ``logs`` directory).
    """
    attach_pageindex_cancel_event(cancel_event)
    prev_cwd = os.getcwd()
    try:
        if log_base:
            os.makedirs(log_base, exist_ok=True)
            os.chdir(log_base)
        return page_index(
            doc,
            model=model,
            toc_check_page_num=toc_check_page_num,
            max_page_num_each_node=max_page_num_each_node,
            max_token_num_each_node=max_token_num_each_node,
            if_add_node_id=if_add_node_id,
            if_add_node_text=if_add_node_text,
            if_add_node_summary=if_add_node_summary,
            if_add_doc_description=if_add_doc_description,
        )
    finally:
        try:
            if log_base:
                os.chdir(prev_cwd)
        except OSError:
            pass
        attach_pageindex_cancel_event(None)


def _discard_pageindex_future(fut: asyncio.Future) -> None:
    """Avoid 'exception never retrieved' when the waiter was cancelled after timeout."""
    if fut.cancelled():
        return
    try:
        exc = fut.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None and not isinstance(exc, PageIndexCancelled):
        logger.debug("PDF page_index executor finished with error: %s", exc)


class _HTMLTextExtractor(HTMLParser):
    """Minimal stdlib HTML→text stripper (no extra deps). Drops script/style
    and collapses visible text — good enough to ingest a web page as markdown."""

    _DROP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._DROP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover - defensive; fall back to raw
        return html
    return parser.text or html


async def _download_url_to_workdir(
    url: str, doc_name: Optional[str], work_dir: str
) -> Tuple[str, str]:
    """Download ``url`` into ``work_dir`` and return ``(local_path, source_url)``.

    The in-process ingest path (the ``pageindex__assimilate`` tool and the
    Python SDK) accepts a URL for ``doc``; this fetches it so the rest of the
    pipeline ingests the real document. The extension is inferred from the
    response content-type (or URL suffix) so PDFs take the PDF page-index path,
    HTML is stripped to markdown text, and office/image files reach Docling.
    The HTTP upload endpoint does its own (richer) download with Google
    Drive/Workspace link rewriting; this is the lighter in-process counterpart.
    """
    from jvspatial.api.exceptions import ValidationError

    from .url_guard import fetch_url_bytes_capped, ssrf_guard_url

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"unsupported URL scheme for ingestion: {url!r}")

    try:
        ssrf_guard_url(url)
        raw, _fname, ctype = await fetch_url_bytes_capped(
            url, read_timeout=30.0, user_agent="jvagent-pageindex/1.0"
        )
        encoding = "utf-8"
    except ValidationError as exc:
        raise ValueError(f"could not download URL for ingestion: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"could not download URL for ingestion: {exc}") from exc

    url_ext = Path(unquote(urlparse(url).path)).suffix.lower()
    if ctype == "application/pdf" or url_ext == ".pdf":
        ext, data = ".pdf", raw
    elif ctype in ("text/html", "application/xhtml+xml") or url_ext in (
        ".html",
        ".htm",
    ):
        ext = ".md"
        data = _html_to_text(raw.decode(encoding, "replace")).encode("utf-8")
    elif (
        url_ext in PAGEINDEX_OFFICE_LIKE_EXTENSIONS
        or url_ext in PAGEINDEX_DOCLING_IMAGE_EXTENSIONS
    ):
        ext, data = url_ext, raw
    else:
        ext = url_ext if url_ext in PAGEINDEX_TEXT_LIKE_EXTENSIONS else ".md"
        data = raw
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=work_dir)
    tmp.write(data)
    tmp.close()
    return tmp.name, url


async def assimilate_document(
    doc: Union[str, Path, bytes, BytesIO],
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
    collection_name: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    doc_description: Optional[str] = None,
    doc_url: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    convert_to_markdown: bool = False,
    ocr: bool = False,
    docling_ocr_engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Assimilate a PDF, Markdown/text, or office document via PageIndex and optionally persist.

    Args:
        doc: File path (str/Path), bytes, or BytesIO. For bytes/BytesIO, extension comes
            from ``doc_name`` (defaulting to ``.pdf`` when missing).
        doc_name: Override document name (default: derived from file; informs extension for bytes)
        model: LLM model for tree generation
        model_action: Optional LanguageModelAction for observability (when in agent context)
        cancel_event: Optional threading.Event; when set, PDF worker thread stops issuing LLM calls (cooperative cancel)
        if_add_node_id: Add node_id to structure
        if_add_node_text: Add text to nodes
        if_add_node_summary: Add summaries (None = use action config via get_pageindex_node_summary)
        if_add_doc_description: Add doc description
        toc_check_page_num: Pages to check for TOC (PDF)
        max_page_num_each_node: Max pages per node (PDF)
        max_token_num_each_node: Max tokens per node (PDF)
        summary_token_threshold: Token threshold for node summaries (default 200)
        persist: Whether to persist to graph database
        collection_name: Collection this document belongs to (default: "default")
        metadata: Custom key-value metadata for filtering at query time
        doc_description: Optional user-provided document description (overrides LLM-generated if set)
        doc_url: Source URL of the document resource (stored on DocumentRootNode for reference citations)
        convert_to_markdown: If True, convert PDF inputs with Docling to Markdown first (requires
            ``jvagent[pageindex]``). Office formats (``.docx``, etc.) always use Docling regardless.
        ocr: When using Docling on PDF or raster images, enable OCR for scanned content.
        docling_ocr_engine: Optional ingest hint (``none`` / ``rapidocr``); jvforge uses RapidOCR only.
            For ``.docx`` / ``.pptx`` with OCR requested, LibreOffice converts to PDF first (when ``soffice``
            is available) so the same OCR path applies as for PDFs.

    Returns:
        Dict with doc_name, structure, doc_description (if requested), _root_id (if persist)
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())

    model = model or "gpt-4o-mini"

    # Normalize: true/yes/1 -> "yes", false/no/0 -> "no" for core; use config when None
    if_add_node_summary = _to_yes_no(if_add_node_summary, get_pageindex_node_summary())
    if_add_node_text = _to_yes_no(if_add_node_text, get_pageindex_node_text())
    if_add_doc_description = _to_yes_no(
        if_add_doc_description, get_pageindex_doc_description()
    )
    if max_token_num_each_node is None:
        max_token_num_each_node = get_pageindex_max_token_num_each_node()
    if summary_token_threshold is None:
        summary_token_threshold = get_pageindex_summary_token_threshold() or 200

    tmp_paths: List[str] = []
    if model_action:
        set_pageindex_model_action(model_action)
    try:
        work_dir = await _ensure_pageindex_work_dir()

        if isinstance(doc, bytes):
            doc = BytesIO(doc)

        if isinstance(doc, BytesIO):
            ext = Path(doc_name or "document.pdf").suffix.lower()
            if not ext:
                ext = ".pdf"
        elif isinstance(doc, (str, Path)):
            doc_str = str(doc)
            looks_like_url = doc_str.startswith(("http://", "https://"))
            if looks_like_url:
                # Fetch the URL so the pipeline ingests the real document
                # (PDF/HTML/office), not the URL string. Carry the source URL
                # onto the document for citations.
                dl_path, dl_url = await _download_url_to_workdir(
                    doc_str, doc_name, work_dir
                )
                tmp_paths.append(dl_path)
                doc = dl_path
                if doc_url is None:
                    doc_url = dl_url
            else:
                # Not a URL and not an existing file → treat as raw content
                # (markdown / plain text) and dump it to a temp file so the rest
                # of the pipeline can read it as a path. This supports callers
                # like the ``pageindex__assimilate`` tool, where the model
                # passes document content directly.
                try:
                    is_existing_file = Path(doc_str).is_file()
                except (OSError, ValueError):
                    # OSError covers "filename too long" — definitively content.
                    is_existing_file = False
                if is_existing_file:
                    from .url_guard import require_path_under_work_dir

                    require_path_under_work_dir(doc_str, work_dir)
                if not is_existing_file and looks_like_doc_path(doc_str):
                    # A path/filename that does not resolve to a file. Do NOT
                    # silently ingest the literal string as the document body
                    # (that loses the real content). Callers that have a file
                    # must resolve it to content/bytes first (the
                    # ``pageindex__assimilate`` tool does this against the user
                    # sandbox); a genuine document must be passed as content.
                    raise ValueError(
                        f"doc {doc_str!r} looks like a file path but no such "
                        "file exists here. Pass the document content directly, "
                        "or a path to a file that exists."
                    )
                if not is_existing_file:
                    inferred_ext = (
                        Path(doc_name).suffix.lower() if doc_name else ""
                    ) or ".md"
                    t_content = tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        suffix=inferred_ext,
                        delete=False,
                        dir=work_dir,
                    )
                    t_content.write(doc_str)
                    t_content.close()
                    tmp_paths.append(t_content.name)
                    doc = t_content.name
            ext = Path(str(doc)).suffix.lower()
        else:
            raise ValueError("doc must be str, Path, bytes, or BytesIO")

        is_pdf = ext == ".pdf"
        force_docling = ext in PAGEINDEX_OFFICE_LIKE_EXTENSIONS
        is_docling_image = ext in PAGEINDEX_DOCLING_IMAGE_EXTENSIONS

        wants_ocr = wants_ooxml_pdf_for_docling_ocr(
            ocr=ocr, docling_ocr_engine=docling_ocr_engine
        )

        if (
            (is_pdf and convert_to_markdown)
            or force_docling
            or (is_docling_image and convert_to_markdown)
        ):
            if isinstance(doc, BytesIO):
                doc.seek(0)
                body = doc.read()
                t_in = tempfile.NamedTemporaryFile(
                    suffix=ext, delete=False, dir=work_dir
                )
                t_in.write(body)
                t_in.close()
                tmp_paths.append(t_in.name)
                src_path = t_in.name
            else:
                src_path = str(doc)

            loop = asyncio.get_running_loop()
            md_text = await loop.run_in_executor(
                None,
                functools.partial(
                    convert_document_to_markdown_sync,
                    src_path,
                    ocr=(
                        wants_ocr
                        if (is_pdf or is_docling_image or force_docling)
                        else False
                    ),
                    docling_ocr_engine=docling_ocr_engine,
                ),
            )
            t_md = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".md",
                delete=False,
                dir=work_dir,
            )
            t_md.write(md_text)
            t_md.close()
            tmp_paths.append(t_md.name)
            doc = t_md.name
            is_pdf = False

        use_pdf_page_index = is_pdf and not convert_to_markdown
        ingest_kind = "pdf_pageindex" if use_pdf_page_index else "markdown_enriched"

        if not use_pdf_page_index and isinstance(doc, BytesIO):
            doc.seek(0)
            text = doc.read().decode("utf-8", errors="replace")
            t_txt = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=ext or ".md",
                delete=False,
                dir=work_dir,
            )
            t_txt.write(text)
            t_txt.close()
            tmp_paths.append(t_txt.name)
            doc = t_txt.name

        if use_pdf_page_index:
            # page_index() uses asyncio.run() internally; run in executor to avoid
            # "asyncio.run() cannot be called from a running event loop"
            from jvagent.core.app import App

            app = await App.get()
            merged = app.file_storage_root_dir if app else None
            log_base = resolve_pageindex_json_log_dir(merged)

            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(
                None,
                functools.partial(
                    _pdf_page_index_worker,
                    cancel_event,
                    doc,
                    log_base=log_base,
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
            fut.add_done_callback(_discard_pageindex_future)
            try:
                result = await fut
            except asyncio.CancelledError:
                signal_pageindex_cancel(cancel_event)
                raise
        else:
            md_path = Path(doc)
            raw_text = md_path.read_text(encoding="utf-8")
            cleaned, line_map = strip_page_markers_and_build_line_page_map(raw_text)
            if cleaned != raw_text:
                t_clean = tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".md",
                    delete=False,
                    dir=work_dir,
                )
                t_clean.write(cleaned)
                t_clean.close()
                tmp_paths.append(t_clean.name)
                path_for_tree = t_clean.name
            else:
                path_for_tree = str(md_path)

            num_lines = cleaned.count("\n") + (1 if cleaned else 0)
            result = await md_to_tree(
                path_for_tree,
                if_add_node_id=if_add_node_id,
                if_add_node_text=if_add_node_text,
                if_add_node_summary=if_add_node_summary,
                if_add_doc_description=if_add_doc_description,
                model=model,
                summary_token_threshold=summary_token_threshold or 200,
            )
            heading_line_offset = int(result.pop("_heading_line_offset", 0) or 0)
            if line_map and result.get("structure"):
                annotate_markdown_structure_pages(
                    result["structure"],
                    line_map,
                    num_lines + heading_line_offset,
                    source_line_offset=heading_line_offset,
                )
            if persist and not result.get("structure"):
                raise ValueError(
                    "Markdown produced no indexable sections (empty or whitespace-only). "
                    "Add headings (# Title) or non-empty body text."
                )

        if result.get("structure"):
            result["structure"] = enrich_structure_titles(result["structure"])

        if result.get("structure"):
            assign_hierarchy_breadcrumbs(result["structure"])
            annotate_content_type_and_enabled(result["structure"])

        name = result.get("doc_name", "")
        if doc_name:
            result["doc_name"] = doc_name
            name = doc_name

        if result.get("doc_name"):
            norm = strip_redundant_md_suffix(str(result["doc_name"]))
            result["doc_name"] = norm
            name = norm

        if result.get("structure"):
            logger.info(
                "pageindex ingest complete ingest_kind=%s doc_name=%s "
                "structure_nodes=%s persist=%s",
                ingest_kind,
                name,
                _count_structure_nodes(result["structure"]),
                persist,
            )

        if persist and result.get("structure"):
            result["collection_name"] = collection_name
            result["metadata"] = metadata
            if doc_description is not None:
                result["doc_description"] = doc_description
            if doc_url is not None:
                result["doc_url"] = doc_url
            elif isinstance(metadata, dict):
                meta_url = metadata.get("doc_url")
                if meta_url is not None and str(meta_url).strip():
                    result["doc_url"] = str(meta_url).strip()
            root_id = await tree_to_graph(result)
            result["_root_id"] = root_id
            logger.info(f"Assimilated document '{name}' (root={root_id})")

        return result
    finally:
        for p in tmp_paths:
            Path(p).unlink(missing_ok=True)
        if model_action:
            set_pageindex_model_action(None)


async def get_document_roots(
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[DocumentRootNode]:
    """Get DocumentRootNodes filtered by collection and optional metadata."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        query: Dict[str, Any] = {"context.collection_name": collection_name}
        query.update(_build_metadata_query(metadata_filter or {}))
        logger.debug(
            "PageIndex get_document_roots: collection=%s query=%s",
            collection_name,
            query,
        )
        roots = await DocumentRootNode.find(query)
        logger.debug(
            "PageIndex get_document_roots: collection=%s returned %d roots",
            collection_name,
            len(roots),
        )
        return roots


async def get_document_root(
    doc_name: str,
    collection_name: str = "default",
) -> Optional[DocumentRootNode]:
    """Get DocumentRootNode by doc_name and collection_name."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        query: Dict[str, Any] = {
            "context.doc_name": doc_name,
            "context.collection_name": collection_name,
        }
        roots = await DocumentRootNode.find(query)
        return roots[0] if roots else None


async def _document_node_counts_by_doc_name(collection_name: str) -> Dict[str, int]:
    """Live DocumentNode counts per doc_name within a collection."""
    nodes = await DocumentNode.find({"context.collection_name": collection_name})
    return dict(Counter(n.doc_name for n in nodes))


async def count_document_chunks(doc_name: str, collection_name: str) -> int:
    """Number of DocumentNode chunks for a single document."""
    nodes = await DocumentNode.find(
        {
            "context.doc_name": doc_name,
            "context.collection_name": collection_name,
        }
    )
    return len(nodes)


async def list_documents(
    collection_name: str = "default",
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """List documents in the PageIndex graph, optionally filtered by collection and metadata."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        query: Dict[str, Any] = {"context.collection_name": collection_name}
        query.update(_build_metadata_query(metadata_filter or {}))
        roots = await DocumentRootNode.find(query)
        counts = await _document_node_counts_by_doc_name(collection_name)
        return [
            {
                "doc_name": r.doc_name,
                "doc_description": r.doc_description,
                "doc_url": r.doc_url,
                "root_id": r.id,
                "collection_name": r.collection_name,
                "metadata": r.metadata,
                "chunks": counts.get(r.doc_name, 0),
            }
            for r in roots
        ]


async def delete_document(
    doc_name: str,
    collection_name: str = "default",
) -> bool:
    """Delete a document and all its nodes from the PageIndex graph."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return False

    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        # Clean up lexical index before cascade-deleting graph nodes
        try:
            from .lexical_index import remove_document_nodes
            from .models import DocumentNode

            nodes = await DocumentNode.find(
                {
                    "context.doc_name": doc_name,
                    "context.collection_name": collection_name,
                }
            )
            if nodes:
                await remove_document_nodes([n.id for n in nodes], collection_name)
        except Exception:
            # Promote to warning: orphan postings linger forever and surface as
            # phantom candidates in subsequent BM25 retrieval. A periodic
            # janitor (lexical_index.remove_collection then reindex_nodes)
            # is the recovery path.
            logger.warning(
                "Lexical index cleanup failed for document deletion (doc=%s, "
                "collection=%s); orphan postings may remain.",
                doc_name,
                collection_name,
                exc_info=True,
            )

        await root.delete()
        logger.info(f"Deleted document '{doc_name}'")
        return True


async def export_documents(
    collection_name: str = "default",
    doc_name: Optional[str] = None,
    root_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Export documents and their graph structure.

    When ``root_id`` is set, exports exactly that DocumentRootNode and its document
    nodes (``root_id`` takes precedence over ``doc_name``). When neither ``root_id``
    nor ``doc_name`` is set, exports the entire collection.
    """
    from .models import DocumentContentEdge, DocumentNode

    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    logger.debug(f"Exporting documents in collection: {collection_name}")
    async with _scoped_pageindex_context(context):
        if root_id:
            root_entity = await DocumentRootNode.get(root_id)
            if root_entity is None or not isinstance(root_entity, DocumentRootNode):
                raise ResourceNotFoundError(f"No document root with id {root_id!r}")
            if root_entity.collection_name != collection_name:
                raise ResourceNotFoundError(
                    f"Document root {root_id!r} is not in collection {collection_name!r}"
                )
            node_query: Dict[str, Any] = {
                "context.collection_name": collection_name,
                "context.doc_name": root_entity.doc_name,
            }
            roots = [root_entity]
            nodes = await DocumentNode.find(node_query)
        else:
            query: Dict[str, Any] = {"context.collection_name": collection_name}
            if doc_name:
                query["context.doc_name"] = doc_name
            roots = await DocumentRootNode.find(query)
            nodes = await DocumentNode.find(query)

        node_ids = {r.id for r in roots} | {n.id for n in nodes}
        all_edges = await DocumentContentEdge.find({})
        edges = [
            e
            for e in all_edges
            if getattr(e, "source", None) in node_ids
            or getattr(e, "target", None) in node_ids
        ]

        chunk_counts = Counter(n.doc_name for n in nodes)
        return {
            "roots": [
                {**r.model_dump(), "chunks": chunk_counts.get(r.doc_name, 0)}
                for r in roots
            ],
            "nodes": [n.model_dump() for n in nodes],
            "edges": [e.model_dump() for e in edges],
        }


async def import_documents(
    data: Dict[str, Any],
    purge: bool = False,
    collection_name: Optional[str] = None,
) -> None:
    """Import documents and their graph structure."""
    from .models import DocumentContentEdge, DocumentNode

    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        if purge and collection_name:
            try:
                from .lexical_index import remove_collection

                await remove_collection(collection_name)
            except Exception:
                logger.debug(
                    "Lexical index cleanup failed during import purge",
                    exc_info=True,
                )
            query = {"context.collection_name": collection_name}
            roots = await DocumentRootNode.find(query)
            for root in roots:
                await root.delete()
        for root_data in data.get("roots", []):
            await DocumentRootNode(**root_data).save()
        imported_nodes: list = []
        for node_data in data.get("nodes", []):
            node = DocumentNode(**node_data)
            await node.save()
            imported_nodes.append(node)
        for edge_data in data.get("edges", []):
            await DocumentContentEdge(**edge_data).save()

        # Build lexical index for imported nodes
        if imported_nodes:
            try:
                from .lexical_index import index_node as _lex_index

                for node in imported_nodes:
                    coll = getattr(
                        node, "collection_name", collection_name or "default"
                    )
                    await _lex_index(
                        node_id=node.id,
                        doc_name=node.doc_name,
                        collection_name=coll,
                        title=node.title or "",
                        text=node.text or "",
                        summary=node.summary or "",
                        prefix_summary=node.prefix_summary or "",
                    )
            except Exception:
                logger.debug("Lexical indexing failed during import", exc_info=True)


def _document_node_to_chunk_dict(node: DocumentNode) -> Dict[str, Any]:
    """Serialize a DocumentNode for chunk list/detail API responses."""
    return {
        "id": node.id,
        "title": node.title or "",
        "text": node.text or "",
        "summary": node.summary,
        "prefix_summary": node.prefix_summary,
        "structure": node.structure or "",
        "node_id": node.node_id or "",
        "start_index": node.start_index,
        "end_index": node.end_index,
        "physical_index": node.physical_index,
        "line_num": node.line_num,
        "doc_name": node.doc_name or "",
        "enabled": node_enabled(node),
        "content_type": getattr(node, "content_type", None),
        "hierarchy": getattr(node, "hierarchy", None),
    }


def _chunk_matches_filter(query: Optional[str], node: DocumentNode) -> bool:
    if not query or not str(query).strip():
        return True
    needle = str(query).strip().lower()
    parts = [
        node.title or "",
        node.text or "",
        node.summary or "",
        node.prefix_summary or "",
        node.structure or "",
    ]
    return any(needle in p.lower() for p in parts)


def _chunk_sort_key(node: DocumentNode) -> tuple:
    return (node.structure or "", node.id or "")


def _chunk_sort_key_collection(node: DocumentNode) -> tuple:
    return (node.doc_name or "", node.structure or "", node.id or "")


def _paginate_filtered_nodes(
    filtered: List[DocumentNode],
    *,
    page: int,
    per_page: int,
) -> Tuple[List[DocumentNode], int]:
    """Slice filtered nodes for the current page; per_page <= 0 means all (capped)."""
    total = len(filtered)
    if page < 1:
        page = 1
    if per_page <= 0:
        cap = min(total, CHUNK_LIST_MAX)
        page_chunks = filtered[:cap]
    else:
        per_page = min(per_page, CHUNK_LIST_MAX)
        start = (page - 1) * per_page
        page_chunks = filtered[start : start + per_page]
    return page_chunks, total


async def _collect_subtree_node_ids(root: DocumentNode) -> List[str]:
    """All DocumentNode ids in the subtree under root (including root), via outgoing edges."""
    ordered: List[str] = []
    seen: set[str] = set()
    queue: List[DocumentNode] = [root]
    while queue:
        current = queue.pop(0)
        if current.id in seen:
            continue
        seen.add(current.id)
        ordered.append(current.id)
        children = await current.outgoing(node=DocumentNode, edge=DocumentContentEdge)
        queue.extend(children)
    return ordered


_CHUNK_UPDATE_FIELDS = frozenset(
    {
        "title",
        "text",
        "summary",
        "prefix_summary",
        "structure",
        "node_id",
        "start_index",
        "end_index",
        "physical_index",
        "line_num",
        "enabled",
        "content_type",
    }
)


async def list_document_chunks(
    doc_name: str,
    collection_name: str,
    *,
    page: int = 1,
    per_page: int = 0,
    q: Optional[str] = None,
    enabled_filter: Optional[bool] = None,
) -> Dict[str, Any]:
    """List DocumentNode chunks for a document with optional text filter and pagination.

    per_page <= 0 means return up to CHUNK_LIST_MAX chunks (all by default, capped).
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return {"chunks": [], "total": 0}

    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        nodes = await DocumentNode.find(
            {
                "context.doc_name": doc_name,
                "context.collection_name": collection_name,
            }
        )

    filtered = [n for n in nodes if _chunk_matches_filter(q, n)]
    if enabled_filter is not None:
        filtered = [n for n in filtered if node_enabled(n) == enabled_filter]
    filtered.sort(key=_chunk_sort_key)
    page_chunks, total = _paginate_filtered_nodes(
        filtered, page=page, per_page=per_page
    )

    return {
        "chunks": [_document_node_to_chunk_dict(n) for n in page_chunks],
        "total": total,
    }


async def list_collection_chunks(
    collection_name: str,
    *,
    page: int = 1,
    per_page: int = 0,
    q: Optional[str] = None,
    enabled_filter: Optional[bool] = None,
) -> Dict[str, Any]:
    """List all DocumentNode chunks in a collection (all documents)."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        nodes = await DocumentNode.find({"context.collection_name": collection_name})

    filtered = [n for n in nodes if _chunk_matches_filter(q, n)]
    if enabled_filter is not None:
        filtered = [n for n in filtered if node_enabled(n) == enabled_filter]
    filtered.sort(key=_chunk_sort_key_collection)
    page_chunks, total = _paginate_filtered_nodes(
        filtered, page=page, per_page=per_page
    )

    return {
        "chunks": [_document_node_to_chunk_dict(n) for n in page_chunks],
        "total": total,
    }


async def patch_document_root(
    doc_name: str,
    collection_name: str,
    fields: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply partial updates to DocumentRootNode. ``fields`` keys may include
    ``metadata`` (dict or None), ``doc_url`` (str or None), and/or ``doc_description``
    (str or None). Must be non-empty."""
    if not fields:
        return None
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=collection_name)
    if not root:
        return None

    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        if "metadata" in fields:
            root.metadata = fields["metadata"]
        if "doc_url" in fields:
            u = fields["doc_url"]
            if u is None:
                root.doc_url = None
            else:
                s = str(u).strip()
                root.doc_url = s or None
        if "doc_description" in fields:
            d = fields["doc_description"]
            if d is None:
                root.doc_description = None
            else:
                s = str(d).strip()
                root.doc_description = s or None
        await root.save()
        return {
            "doc_name": root.doc_name,
            "root_id": root.id,
            "metadata": root.metadata,
            "doc_url": root.doc_url,
            "doc_description": root.doc_description,
        }


async def update_document_metadata(
    doc_name: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Set DocumentRootNode.metadata (None clears)."""
    return await patch_document_root(doc_name, collection_name, {"metadata": metadata})


async def get_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
) -> Optional[Dict[str, Any]]:
    """Return chunk dict if the node exists and belongs to doc_name/collection."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        node = await DocumentNode.get(chunk_id)

    if not node:
        return None
    if node.doc_name != doc_name or node.collection_name != collection_name:
        return None
    return _document_node_to_chunk_dict(node)


async def update_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply whitelisted field updates; refresh lexical index for this node."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        node = await DocumentNode.get(chunk_id)
        if (
            not node
            or node.doc_name != doc_name
            or node.collection_name != collection_name
        ):
            return None

        for key, value in updates.items():
            if key not in _CHUNK_UPDATE_FIELDS:
                continue
            if not hasattr(node, key):
                continue
            if key == "enabled" and value is not None and not isinstance(value, bool):
                value = bool(value)
            if key == "content_type":
                if value is None or value == "":
                    value = None
                elif not isinstance(value, str):
                    value = str(value) if value is not None else None
                else:
                    value = value.strip() or None
            setattr(node, key, value)

        await node.save()

        try:
            from .lexical_index import index_node, remove_node

            await remove_node(node.id, collection_name)
            await index_node(
                node_id=node.id,
                doc_name=node.doc_name,
                collection_name=collection_name,
                title=node.title or "",
                text=node.text or "",
                summary=node.summary or "",
                prefix_summary=node.prefix_summary or "",
            )
        except Exception:
            logger.warning(
                "Lexical index refresh failed after chunk update (chunk=%s, "
                "doc=%s); subsequent BM25 retrieval may return stale entries.",
                node.id,
                node.doc_name,
                exc_info=True,
            )

        return _document_node_to_chunk_dict(node)


async def delete_document_chunk(
    chunk_id: str,
    doc_name: str,
    collection_name: str,
    *,
    cascade: bool = True,
) -> bool:
    """Delete a chunk node; optionally cascade to descendants. Cleans lexical index first."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    context = _get_pageindex_context()
    async with _scoped_pageindex_context(context):
        node = await DocumentNode.get(chunk_id)
        if (
            not node
            or node.doc_name != doc_name
            or node.collection_name != collection_name
        ):
            return False

        try:
            from .lexical_index import remove_document_nodes

            if cascade:
                ids = await _collect_subtree_node_ids(node)
            else:
                ids = [node.id]
            await remove_document_nodes(ids, collection_name)
        except Exception:
            logger.warning(
                "Lexical index cleanup failed before chunk delete (chunk=%s, "
                "doc=%s); orphan postings may remain.",
                chunk_id,
                doc_name,
                exc_info=True,
            )

        await node.delete(cascade=cascade)
        logger.info(
            "Deleted PageIndex chunk %s (doc=%s, cascade=%s)",
            chunk_id,
            doc_name,
            cascade,
        )
        return True
