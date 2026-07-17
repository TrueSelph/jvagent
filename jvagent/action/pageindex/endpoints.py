"""PageIndex document ingestion and management endpoints.

Vectorless RAG: ingest PDF, Markdown/text, and office documents; list, search, delete,
export/import documents.
All routes are agent-scoped (collection = agent_id from path unless noted).
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from fastapi import Query, Request
from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.storage.exceptions import InvalidPathError, PathTraversalError
from jvspatial.storage.security import PathSanitizer
from pydantic import Field
from python_multipart.multipart import FormParser, parse_options_header

from jvagent.core.agent import Agent
from jvagent.env import get_jvagent_jvforge_base_url

from .config import get_pageindex_node_summary, initialize_pageindex_database
from .documents import (
    CHUNK_LIST_MAX,
    PAGEINDEX_OFFICE_LIKE_EXTENSIONS,
    PAGEINDEX_TEXT_LIKE_EXTENSIONS,
    PAGEINDEX_UPLOAD_EXTENSIONS,
    _ensure_pageindex_work_dir,
    assimilate_document,
    count_document_chunks,
    delete_document,
    delete_document_chunk,
    export_documents,
    get_document_chunk,
    get_document_root,
    import_documents,
    list_collection_chunks,
    list_document_chunks,
    list_documents,
    patch_document_root,
    update_document_chunk,
)
from .jvforge_assimilate import assimilate_via_jvforge, assimilate_via_jvforge_async
from .jvforge_routing import resolve_effective_jvforge_base
from .pageindex_action import (
    PageIndexAction,
    ensure_ingestion_config_for_agent,
)
from .retrieval import search_documents

# Re-exported for tests/back-compat (the guard's canonical home is url_guard).
from .url_guard import (
    MAX_UPLOAD_BYTES,
)
from .url_guard import fetch_url_bytes_capped as _fetch_url_bytes_capped
from .url_guard import ssrf_guard_url as _ssrf_guard_url  # noqa: F401,E402

logger = logging.getLogger(__name__)

# Strong references to in-flight fire-and-forget tasks (asyncio only keeps weak
# ones). AUDIT-actions (LOW).
_BACKGROUND_TASKS: set = set()

ALLOWED_EXTENSIONS = PAGEINDEX_UPLOAD_EXTENSIONS


def _strip_nonempty(label: str, value: Optional[str]) -> str:
    """Return stripped string or raise ValidationError if empty."""
    s = (value or "").strip()
    if not s:
        raise ValidationError(
            f"{label} is required",
            details={label: value},
        )
    return s


async def _get_pageindex_action(agent_id: str) -> PageIndexAction:
    """Load the agent's PageIndexAction or raise."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action = await agent.get_action_by_type("PageIndexAction")
    if not action or not isinstance(action, PageIndexAction):
        raise ResourceNotFoundError(
            message=f"No PageIndexAction found for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )
    return action


async def _pageindex_llm_webhook_url_for_jvforge(agent_id: str) -> str:
    """LLM callback URL for jvforge (persisted on ``PageIndexAction``)."""
    core = await _get_pageindex_action(agent_id)
    return await core.get_webhook_url()


async def _get_app_id_from_node() -> Optional[str]:
    """Get app_id from App node. JVAGENT_APP_ID env overrides when set in config."""
    from jvagent.core.app import App

    app = await App.get()
    return getattr(app, "app_id", None) if app else None


def _parse_metadata(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse metadata JSON string. Returns None if empty or invalid."""
    if not value or not value.strip():
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _form_yes_no_optional(value: Optional[str]) -> Optional[bool]:
    """Parse optional multipart yes/no to bool; unknown or empty -> None."""
    if value is None or not str(value).strip():
        return None
    v = str(value).lower().strip()
    if v in ("yes", "true", "1"):
        return True
    if v in ("no", "false", "0"):
        return False
    return None


def _resolve_docling_ocr_for_ingest(
    docling_ocr_engine_raw: Optional[str],
    ocr_raw: Optional[str],
) -> tuple[bool, Optional[str]]:
    """Aligned with ``jvforge.multipart_ingest.resolve_docling_ocr_for_ingest``."""
    de = (docling_ocr_engine_raw or "").strip().lower()
    if de:
        if de in ("none", "off", "no", "false", "0"):
            return False, None
        return True, "rapidocr"
    ocr_opt = _form_yes_no_optional(ocr_raw)
    ocr_flag = False if ocr_opt is None else ocr_opt
    return ocr_flag, None


def _form_int_optional(value: Optional[str]) -> Optional[int]:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_chunk_enabled_filter(raw: Optional[str]) -> Optional[bool]:
    """Tri-state: None = all chunks; True = RAG-enabled only; False = disabled only."""
    if raw is None or not str(raw).strip():
        return None
    v = str(raw).lower().strip()
    if v in ("true", "1", "yes", "enabled"):
        return True
    if v in ("false", "0", "no", "disabled"):
        return False
    return None


def _filename_from_content_disposition(cd: Optional[str]) -> Optional[str]:
    if not cd:
        return None
    for part in cd.split(";"):
        part = part.strip()
        low = part.lower()
        if low.startswith("filename*="):
            try:
                _, _, value = part.partition("=")
                value = value.strip()
                if value.lower().startswith("utf-8''"):
                    return unquote(value[7:].strip().strip('"'))
            except Exception:
                continue
        if low.startswith("filename="):
            _, _, value = part.partition("=")
            v = value.strip().strip('"')
            if v:
                return v
    return None


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = unquote(Path(path).name)
    return name if name else "download"


def _normalize_google_workspace_file_url(
    url: str,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Rewrite Docs/Sheets/Slides *viewer* URLs to an export URL.

    Viewer links (e.g. ``/document/d/<id>/edit`` or ``/document/u/0/d/<id>/edit``) return HTML
    without a useful file extension. Export links return binary office formats that match
    ``ALLOWED_EXTENSIONS``.

    **Note:** The file must be reachable without Google OAuth from the server (for example,
    "Anyone with the link can view"). Otherwise the download may return 403 or HTML.

    Returns ``(fetch_url, export_ext, doc_id)``. When no rewrite applies, returns
    ``(original_url, None, None)``. ``export_ext`` is a dotted suffix (``.docx``, etc.).
    ``doc_id`` is used only as a filename fallback when the download has no usable name.
    """
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return raw, None, None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw, None, None
    host = (parsed.hostname or "").lower()
    if host not in ("docs.google.com", "www.docs.google.com"):
        return raw, None, None
    path = parsed.path or ""
    segments = [p for p in path.split("/") if p]
    if segments and segments[-1] == "export":
        return raw, None, None
    # Signed-in users often get /document/u/0/d/<id>/... (or /u/1/, etc.); plain share links omit /u/N/.
    m = re.search(
        r"/(document|spreadsheets|presentation)(?:/u/\d+)?/d/([a-zA-Z0-9_-]+)",
        path,
    )
    if not m:
        return raw, None, None
    kind, doc_id = m.group(1), m.group(2)
    if kind == "document":
        export_fmt, ext = "docx", ".docx"
    elif kind == "spreadsheets":
        export_fmt, ext = "xlsx", ".xlsx"
    else:
        export_fmt, ext = "pptx", ".pptx"
    fetch_url = (
        f"{parsed.scheme}://{parsed.netloc}/"
        f"{kind}/d/{doc_id}/export?format={quote(export_fmt)}"
    )
    return fetch_url, ext, doc_id


def _normalize_google_drive_file_url(url: str) -> Optional[str]:
    """Rewrite ``drive.google.com`` file links to the direct download endpoint.

    Paths like ``/file/d/<id>/view`` return HTML. ``/uc?export=download&id=<id>`` returns file
    bytes when the item is shared such that the server can read it without Google sign-in.

    Returns the download URL, or ``None`` when no rewrite applies (including when the URL is
    already a ``/uc`` download request).
    """
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ("drive.google.com", "www.drive.google.com"):
        return None
    path_noslash = (parsed.path or "").rstrip("/")
    qs = parse_qs(parsed.query)
    if path_noslash == "/uc" or path_noslash.endswith("/uc"):
        export = (qs.get("export") or [None])[0]
        if export == "download" and qs.get("id"):
            return None
    file_id: Optional[str] = None
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", parsed.path or "")
    if m:
        file_id = m.group(1)
    if not file_id and path_noslash == "/open":
        ids = qs.get("id")
        if ids:
            file_id = ids[0]
    if not file_id:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/uc?export=download&id={quote(file_id)}"


def _safe_ingest_filename_stem(stem: str) -> str:
    """Make a single path segment usable as the base name for an ingested file."""
    s = (stem or "").strip()
    for sep in ("/", "\\"):
        s = s.replace(sep, "_")
    s = s.replace("\x00", "")
    s = s.strip().strip(".")
    if not s:
        s = "document"
    return s[:200]


def _is_pageindex_graph_artifact_url(url: str) -> bool:
    """True for jvforge ``GET /v1/artifacts/{job_id}`` graph JSON (path has no ``.json`` suffix)."""
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    try:
        path = urlparse(raw).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3:
            return False
        if parts[-3] != "v1" or parts[-2] != "artifacts":
            return False
        uuid.UUID(parts[-1])
        return True
    except (ValueError, IndexError):
        return False


def _safe_pageindex_relative_path(*segments: str) -> str:
    rel = "/".join(segments)
    try:
        return PathSanitizer.sanitize_path(rel.replace("\\", "/"))
    except (InvalidPathError, PathTraversalError) as e:
        raise ValidationError(f"Invalid storage path: {e}")


def _resolve_ingest_filename(filename_hint: str, content_type: Optional[str]) -> str:
    name = filename_hint or "download"
    ext = Path(name).suffix.lower()
    if ext in ALLOWED_EXTENSIONS:
        return name
    guessed: Optional[str] = None
    if content_type:
        main = content_type.strip()
        if main in ("text/markdown", "text/x-markdown"):
            guessed = ".md"
        else:
            guessed = mimetypes.guess_extension(main)
            if guessed == ".jpe":
                guessed = ".jpeg"
    if guessed and guessed.lower() in ALLOWED_EXTENSIONS:
        stem = Path(name).stem or "download"
        return f"{stem}{guessed.lower()}"
    raise ValidationError(
        f"Could not determine an allowed file type from URL or Content-Type. "
        f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    )


async def _save_pageindex_staging(
    subdir: str,
    agent_id: str,
    content: bytes,
    original_filename: str,
    extra_metadata: Dict[str, Any],
) -> str:
    from jvagent.core.app import App

    app = await App.get()
    if not app:
        raise ValidationError("File storage unavailable")
    now_dt = await app.now()
    timestamp = now_dt.strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    ext = Path(original_filename).suffix.lower() or ".bin"
    file_part = f"{timestamp}_{uid}{ext}"
    storage_path = _safe_pageindex_relative_path(subdir, agent_id, file_part)
    meta = {
        **extra_metadata,
        "original_filename": original_filename,
        "size": len(content),
        "created_at": now_dt.isoformat(),
    }
    ok = await app.save_file(storage_path, content, metadata=meta)
    if not ok:
        raise ValidationError("Failed to save downloaded file to storage")
    return storage_path


async def _delete_staged_file(storage_path: Optional[str]) -> None:
    if not storage_path:
        return
    from jvagent.core.app import App

    app = await App.get()
    if app:
        await app.delete_file(storage_path)


def _import_staging_filename(url: str, content_type: Optional[str]) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in (".json", ".yaml", ".yml"):
        return f"import{ext}"
    if content_type:
        low = content_type.lower()
        if "yaml" in low:
            return "import.yaml"
        if "json" in low:
            return "import.json"
    return "import.json"


def _coerce_import_payload_to_dict(data: Any) -> Dict[str, Any]:
    if isinstance(data, str):
        s = data.strip()
        # Prefer JSON for strings that look like JSON. yaml.safe_load can yield a list for
        # valid graph JSON in edge cases; json.loads matches PageIndex export shape reliably.
        if s and s[0] in "{[":
            try:
                parsed_json = json.loads(s)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed_json, dict):
                    return parsed_json
                raise ValidationError(
                    "PageIndex import payload must be a single JSON object with graph keys "
                    "(e.g. roots, nodes, edges), not a top-level array or scalar."
                )
        try:
            import yaml

            parsed = yaml.safe_load(data)
        except (ImportError, yaml.YAMLError):
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON/YAML format: {e}")
    elif isinstance(data, dict):
        parsed = data
    else:
        raise ValidationError("Data must be a JSON object or YAML string")
    if not isinstance(parsed, dict):
        raise ValidationError("Data must be a dictionary")
    return parsed


async def _run_import_parsed(
    agent_id: str, parsed: Dict[str, Any], purge: bool
) -> None:
    for root in parsed.get("roots", []):
        root["collection_name"] = agent_id
        ctx = root.get("context")
        if isinstance(ctx, dict):
            ctx["collection_name"] = agent_id
    for node in parsed.get("nodes", []):
        node["collection_name"] = agent_id
        ctx = node.get("context")
        if isinstance(ctx, dict):
            ctx["collection_name"] = agent_id
    await import_documents(parsed, purge=purge, collection_name=agent_id)


async def _stage_graph_from_remote_url(
    agent_id: str,
    url: str,
    *,
    staging_subdir: str = "pageindex_import",
    extra_staging_metadata: Optional[Dict[str, Any]] = None,
    fetch_read_timeout: float = 120.0,
) -> str:
    """Download a PageIndex graph from ``url`` into app storage; return storage path.

    Retries on 404 (artifact may not be written yet or propagating across replicas)
    with exponential backoff up to 30 seconds.

    Caller must delete the staged file after import (or on failure).
    """
    _ARTIFACT_404_RETRIES = 6
    _ARTIFACT_404_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 10.0, 5.0)
    for attempt in range(1, _ARTIFACT_404_RETRIES + 1):
        try:
            raw, _fname_hint, ct = await _fetch_url_bytes_capped(
                url, read_timeout=fetch_read_timeout
            )
            break
        except ValidationError as exc:
            msg = str(getattr(exc, "message", exc) or exc)
            if "HTTP 404" in msg and attempt < _ARTIFACT_404_RETRIES:
                delay = _ARTIFACT_404_BACKOFF_S[
                    min(attempt - 1, len(_ARTIFACT_404_BACKOFF_S) - 1)
                ]
                logger.warning(
                    "Artifact URL returned 404 (attempt %d/%d); retrying in %.1fs: %s",
                    attempt,
                    _ARTIFACT_404_RETRIES,
                    delay,
                    url,
                )
                await asyncio.sleep(delay)
                continue
            raise
    staging_fn = _import_staging_filename(url, ct)
    meta: Dict[str, Any] = {"source_url": url, "agent_id": agent_id}
    if extra_staging_metadata:
        meta = {**meta, **extra_staging_metadata}
    return await _save_pageindex_staging(
        staging_subdir,
        agent_id,
        raw,
        staging_fn,
        meta,
    )


async def _import_graph_from_staged_storage_path(
    agent_id: str,
    staged_path: str,
    *,
    purge: bool,
) -> None:
    """Load a staged PageIndex graph file and import it (does not delete ``staged_path``)."""
    from jvagent.core.app import App

    app = await App.get()
    if not app:
        raise ValidationError("File storage unavailable")
    raw = await app.get_file(staged_path)
    if not raw:
        raise ValidationError("Staged import file missing or empty")
    text = raw.decode("utf-8", errors="replace")
    parsed = _coerce_import_payload_to_dict(text)
    await _run_import_parsed(agent_id, parsed, purge)


def _schedule_background_webhook_graph_import(
    agent_id: str,
    staged_path: str,
    *,
    purge: bool,
    process_url: str,
) -> None:
    """Run heavy graph import off the webhook request so jvforge gets a prompt HTTP response."""

    async def _job() -> None:
        try:
            await _import_graph_from_staged_storage_path(
                agent_id, staged_path, purge=purge
            )
            logger.info(
                "PageIndex process_document_url background import finished agent_id=%s source=%s",
                agent_id,
                (process_url[:160] + "…") if len(process_url) > 160 else process_url,
            )
        except Exception as e:
            logger.error(
                "PageIndex process_document_url background import failed agent_id=%s: %s",
                agent_id,
                e,
                exc_info=True,
            )
        finally:
            await _delete_staged_file(staged_path)

    # Retain a strong reference until the task finishes: asyncio only holds a
    # weak reference, so a bare create_task() can be garbage-collected mid-flight
    # (documented footgun), silently dropping the background import.
    _task = asyncio.create_task(_job())
    _BACKGROUND_TASKS.add(_task)
    _task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _import_graph_from_remote_url(
    agent_id: str,
    url: str,
    *,
    purge: bool = False,
    staging_subdir: str = "pageindex_import",
    extra_staging_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Download PageIndex graph JSON/YAML from ``url`` and import into ``agent_id`` collection.

    Stages a copy under App file storage (for audit), then deletes it in ``finally``.
    """
    staged_path: Optional[str] = None
    try:
        staged_path = await _stage_graph_from_remote_url(
            agent_id,
            url,
            staging_subdir=staging_subdir,
            extra_staging_metadata=extra_staging_metadata,
            fetch_read_timeout=120.0,
        )
        await _import_graph_from_staged_storage_path(agent_id, staged_path, purge=purge)
    finally:
        await _delete_staged_file(staged_path)


def _parse_multipart_safe(body: bytes, content_type: str) -> tuple[
    bytes,
    str,
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
]:
    """Parse multipart form-data from raw body without decoding file content.

    Returns (file_content, filename, doc_name, model, if_add_node_summary,
             collection_name, metadata, doc_description, doc_url,
             convert_to_markdown, ocr, docling_ocr_engine, normalize_bold_headings, file_url,
             use_jvforge).
    Uses latin-1 for headers to avoid UTF-8 decode errors on non-ASCII filenames or field values.
    """
    content_type_bytes = (
        content_type.encode("latin-1")
        if isinstance(content_type, str)
        else content_type
    )
    ctype, params = parse_options_header(content_type_bytes)
    if ctype != b"multipart/form-data":
        raise ValidationError("Expected multipart/form-data")
    boundary = params.get(b"boundary")
    if not boundary:
        raise ValidationError("Missing boundary in multipart request")

    file_content = b""
    filename = ""
    doc_name: Optional[str] = None
    model: Optional[str] = None
    if_add_node_summary: Optional[str] = None
    collection_name: Optional[str] = None
    metadata_raw: Optional[str] = None
    doc_description: Optional[str] = None
    doc_url: Optional[str] = None
    convert_to_markdown: Optional[str] = None
    ocr: Optional[str] = None
    docling_ocr_engine: Optional[str] = None
    normalize_bold_headings: Optional[str] = None
    file_url: Optional[str] = None
    use_jvforge: Optional[str] = None

    def _safe_str(b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")

    def on_field(field) -> None:
        nonlocal doc_name, model, if_add_node_summary, collection_name, metadata_raw, doc_description, doc_url, convert_to_markdown, ocr, docling_ocr_engine, normalize_bold_headings, file_url, use_jvforge
        name = _safe_str(field.field_name) if field.field_name else ""
        val = field.value
        value = _safe_str(val) if val is not None else ""
        if name == "doc_name":
            doc_name = value or None
        elif name == "model":
            model = value or None
        elif name == "if_add_node_summary":
            if_add_node_summary = value or None
        elif name == "collection_name":
            collection_name = value or None
        elif name == "metadata":
            metadata_raw = value or None
        elif name == "doc_description":
            doc_description = value or None
        elif name == "doc_url":
            doc_url = value or None
        elif name == "convert_to_markdown":
            convert_to_markdown = value or None
        elif name == "ocr":
            ocr = value or None
        elif name == "docling_ocr_engine":
            docling_ocr_engine = value or None
        elif name == "normalize_bold_headings":
            normalize_bold_headings = value or None
        elif name == "file_url":
            file_url = value or None
        elif name == "use_jvforge":
            use_jvforge = value or None

    def on_file(f) -> None:
        nonlocal file_content, filename
        filename = _safe_str(f.file_name) if f.file_name else ""
        f.file_object.seek(0)
        file_content = f.file_object.read()

    parser = FormParser(
        content_type="multipart/form-data",
        on_field=on_field,
        on_file=on_file,
        boundary=boundary,
    )
    stream = BytesIO(body)
    chunk_size = 65536
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        parser.write(chunk)
    parser.finalize()
    return (
        file_content,
        filename,
        doc_name,
        model,
        if_add_node_summary,
        collection_name,
        metadata_raw,
        doc_description,
        doc_url,
        convert_to_markdown,
        ocr,
        docling_ocr_engine,
        normalize_bold_headings,
        file_url,
        use_jvforge,
    )


async def _do_assimilate(
    content: bytes,
    ext: str,
    *,
    doc_name: Optional[str] = None,
    model: Optional[str] = None,
    if_add_node_summary: Optional[str] = None,
    collection_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    doc_description: Optional[str] = None,
    doc_url: Optional[str] = None,
    convert_to_markdown: bool = False,
    ocr: bool = False,
    docling_ocr_engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Run assimilate_document on content. Temps live under ``pageindex/tmp`` (file_storage root)."""
    assimilate_kw = {
        "doc_name": doc_name,
        "model": model,
        "if_add_node_summary": if_add_node_summary,
        "collection_name": collection_name,
        "metadata": metadata,
        "doc_description": doc_description,
        "doc_url": doc_url,
        "convert_to_markdown": convert_to_markdown,
        "ocr": ocr,
        "docling_ocr_engine": docling_ocr_engine,
    }

    work_dir = await _ensure_pageindex_work_dir()

    if ext == ".pdf":
        doc = BytesIO(content)
        try:
            return await assimilate_document(doc, **assimilate_kw)
        except UnicodeDecodeError as e:
            logger.warning(f"PDF processing failed with UnicodeDecodeError: {e}")
            raise ValidationError(
                "Failed to process PDF. The file may be corrupted or contain "
                "unsupported encoding."
            )

    if ext in PAGEINDEX_TEXT_LIKE_EXTENSIONS:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=ext,
            delete=False,
            dir=work_dir,
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        try:
            return await assimilate_document(tmp_path, **assimilate_kw)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    if ext in PAGEINDEX_OFFICE_LIKE_EXTENSIONS:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
            dir=work_dir,
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            return await assimilate_document(tmp_path, **assimilate_kw)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    raise ValidationError(
        f"Invalid file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    )


# PageIndex API: agent-scoped routes only (collection = agent_id from path)
@endpoint(
    "/agents/{agent_id}/pageindex/documents",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "doc_name": ResponseField(
                field_type=str,
                description="Document identifier",
                example="my_document",
            ),
            "root_id": ResponseField(
                field_type=str,
                description="Document root node ID",
                example="n.DocumentRootNode.abc123",
            ),
            "doc_description": ResponseField(
                field_type=Optional[str],
                description="Optional document description",
                example=None,
            ),
            "chunks": ResponseField(
                field_type=int,
                description="Number of indexed chunks (DocumentNodes); 0 when async or not yet imported",
                example=42,
            ),
        }
    ),
)
async def ingest_document_endpoint(
    request: Request,
    agent_id: str,
) -> Dict[str, Any]:
    """Ingest a document into the agent's PageIndex collection.

    **Request:** `multipart/form-data`

    | Field | Type | Required | Description |
    |-------|------|----------|-------------|
    | file | File | One of file / file_url | `.pdf`, `.md`, `.markdown`, `.txt`, or office (`.docx`, `.doc`, `.xls`, `.xlsx`, `.ppt`, `.pptx`) |
    | file_url | string | One of file / file_url | HTTPS/HTTP URL to a **document** (pdf, md, …), or a **jvforge** graph URL ``.../v1/artifacts/{job_id}`` (JSON export). Without ``JVAGENT_JVFORGE_BASE_URL``, this server downloads, normalizes links, and ingests. When jvforge is configured, the URL is forwarded to jvforge (``file_url`` on ``POST /v1/process`` or ``/v1/jobs``) and **jvforge** downloads and normalizes (Google Docs → Markdown export on jvforge; Sheets/Slides → office exports; Drive ``/file/d/…`` → direct download). Shared content must be fetchable without signing in from the fetching server. |
    | doc_name | string | No | Document identifier (default: derived from filename). For Google Docs/Sheets/Slides ``file_url`` ingests, when set, this value is also used as the downloaded file’s base name (with the correct extension) instead of the Drive id or export filename. |
    | doc_description | string | No | Human-readable document description |
    | doc_url | string | No | Source URL for reference citations (default when using file_url: the same download URL) |
    | if_add_node_summary | string | No | "yes" or "no" – generate LLM summaries per node (default: from agent's PageIndex config) |
    | convert_to_markdown | string | No | "yes" or "no" – use Docling to convert PDF to Markdown first (default: no) |
    | ocr | string | No | "yes" or "no" – enable OCR when using Docling on PDF (default: no). Ignored when ``docling_ocr_engine`` is set. |
    | docling_ocr_engine | string | No | ``none`` or ``rapidocr`` – RapidOCR (ONNX) on jvforge / local Docling when ``convert_to_markdown`` is on. Legacy names map to ``rapidocr``. When set, overrides ``ocr`` yes/no. |
    | normalize_bold_headings | string | No | "yes" or "no" — sparse bold→``##`` normalization on **jvforge** only; requires ``JVAGENT_JVFORGE_BASE_URL`` (validation error if ``yes`` without it). Default: no |
    | use_jvforge | string | No | "yes" or "no" — when "no", ingest on this server even if ``JVAGENT_JVFORGE_BASE_URL`` is set. When "yes", require jvforge URL. Omit for legacy behavior (use server env only). |
    | metadata | string | No | JSON object for tagging, e.g. `{"topic": "finance", "year": 2024}` |

    **Response:** `doc_name`, `root_id`, `doc_description`, `chunks` (chunk count when the graph
    is available). With ``JVAGENT_JVFORGE_ASYNC=true``, responses also include ``status``,
    ``job_id``, ``queue_position``, and ``message``; ``root_id`` is empty, ``doc_description`` is
    null, and ``chunks`` is 0 until the webhook finishes importing the graph.

    Documents are stored in the agent's collection (collection = `agent_id` from path).

    **Default (native) processing:** When ``JVAGENT_JVFORGE_BASE_URL`` is unset, this server
    always performs download (for ``file_url``), normalization, and graph build locally—the
    same path as multipart file uploads. jvforge is optional and only changes where heavy
    work runs when the URL is set.

    When ``JVAGENT_JVFORGE_BASE_URL`` is set, ingestion is delegated to that jvforge service
    (``POST /v1/process`` or async ``POST /v1/jobs``). ``llm_webhook_url`` is taken from
    ``PageIndexAction`` (jvforge calls back for node-summary LLMs). For ``file_url``, the URL is
    sent to jvforge as a form field (no agent-side download). Set ``JVAGENT_JVFORGE_API_KEY`` if you
    add ingress auth in front of jvforge.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise ValidationError("Expected multipart/form-data")

    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File too large ({len(body)} bytes). Maximum upload size is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    (
        content,
        filename,
        doc_name,
        model,
        if_add_node_summary,
        collection_name,
        metadata_raw,
        doc_description,
        doc_url,
        convert_to_markdown_raw,
        ocr_raw,
        docling_ocr_engine_raw,
        normalize_bold_headings_raw,
        file_url_raw,
        use_jvforge_raw,
    ) = _parse_multipart_safe(body, content_type)
    collection_name = collection_name or agent_id
    metadata = _parse_metadata(metadata_raw)

    if if_add_node_summary is None:
        await ensure_ingestion_config_for_agent(agent_id)

    convert_opt = _form_yes_no_optional(convert_to_markdown_raw)
    convert_to_markdown = False if convert_opt is None else convert_opt
    ocr_flag, docling_ocr_engine_eff = _resolve_docling_ocr_for_ingest(
        docling_ocr_engine_raw,
        ocr_raw,
    )
    bold_opt = _form_yes_no_optional(normalize_bold_headings_raw)
    normalize_bold_flag = False if bold_opt is None else bold_opt

    file_url = (file_url_raw or "").strip()
    has_upload = len(content) > 0
    if file_url and has_upload:
        raise ValidationError("Provide either file or file_url, not both")
    if not file_url and not has_upload:
        raise ValidationError("Provide a file upload or file_url")

    forge_base = (get_jvagent_jvforge_base_url() or "").strip()
    use_jvforge_opt = _form_yes_no_optional(use_jvforge_raw)
    effective_forge = resolve_effective_jvforge_base(
        forge_base, use_jvforge=use_jvforge_opt
    )

    staged_path: Optional[str] = None
    try:
        if file_url and _is_pageindex_graph_artifact_url(file_url):
            before_docs = await list_documents(
                collection_name=agent_id, metadata_filter=None
            )
            before_names: Set[str] = {
                n for d in before_docs if (n := (d.get("doc_name") or "").strip())
            }
            await _import_graph_from_remote_url(
                agent_id,
                file_url,
                purge=False,
                staging_subdir="pageindex_ingest",
            )
            after_docs = await list_documents(
                collection_name=agent_id, metadata_filter=None
            )
            new_docs = [
                d
                for d in after_docs
                if (d.get("doc_name") or "").strip() not in before_names
            ]
            pick: Optional[Dict[str, Any]] = None
            effective_dn = (doc_name or "").strip()
            if effective_dn:
                for d in new_docs:
                    if (d.get("doc_name") or "").strip() == effective_dn:
                        pick = d
                        break
                if pick is None:
                    for d in after_docs:
                        if (d.get("doc_name") or "").strip() == effective_dn:
                            pick = d
                            break
            if pick is None and len(new_docs) == 1:
                pick = new_docs[0]
            elif pick is None and new_docs:
                pick = new_docs[-1]
            return {
                "doc_name": (pick or {}).get("doc_name") or effective_dn or "",
                "root_id": (pick or {}).get("root_id", ""),
                "doc_description": (
                    (pick or {}).get("doc_description") if pick else None
                ),
                "chunks": int((pick or {}).get("chunks") or 0),
            }

        if file_url and effective_forge:
            doc_url_effective = (doc_url or "").strip() or file_url
            effective_doc_name = (doc_name or "").strip()
            async_mode = (
                os.environ.get("JVAGENT_JVFORGE_ASYNC", "false").lower() == "true"
            )
            try:
                llm_wh_url = await _pageindex_llm_webhook_url_for_jvforge(agent_id)
                summary_for_forge = if_add_node_summary
                if summary_for_forge is None:
                    summary_for_forge = "yes" if get_pageindex_node_summary() else "no"

                if async_mode:
                    result = await assimilate_via_jvforge_async(
                        base_url=effective_forge,
                        agent_id=agent_id,
                        doc_name=effective_doc_name,
                        model=model,
                        if_add_node_summary=summary_for_forge,
                        collection_name=collection_name,
                        metadata=metadata,
                        doc_description=doc_description,
                        doc_url=doc_url_effective or None,
                        convert_to_markdown=convert_to_markdown,
                        ocr=ocr_flag,
                        docling_ocr_engine=docling_ocr_engine_eff,
                        normalize_bold_headings=normalize_bold_flag,
                        llm_webhook_url=llm_wh_url,
                        emergency=False,
                        file_url=file_url,
                        filename=None,
                        content=None,
                    )
                    return {
                        "status": result["status"],
                        "job_id": result["job_id"],
                        "queue_position": result["queue_position"],
                        "doc_name": result["doc_name"],
                        "message": result["message"],
                        "root_id": "",
                        "doc_description": None,
                        "chunks": 0,
                    }
                result = await assimilate_via_jvforge(
                    base_url=effective_forge,
                    agent_id=agent_id,
                    doc_name=effective_doc_name,
                    model=model,
                    if_add_node_summary=summary_for_forge,
                    collection_name=collection_name,
                    metadata=metadata,
                    doc_description=doc_description,
                    doc_url=doc_url_effective or None,
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr_flag,
                    docling_ocr_engine=docling_ocr_engine_eff,
                    normalize_bold_headings=normalize_bold_flag,
                    llm_webhook_url=llm_wh_url,
                    file_url=file_url,
                    filename=None,
                    content=None,
                )
            except ImportError as e:
                raise ValidationError(str(e))
            except ValueError as e:
                raise ValidationError(str(e))

            doc_name_out = result.get("doc_name", "")
            chunks_out = (
                await count_document_chunks(doc_name_out, collection_name)
                if doc_name_out
                else 0
            )
            return {
                "doc_name": doc_name_out,
                "root_id": result.get("_root_id", ""),
                "doc_description": result.get("doc_description"),
                "chunks": chunks_out,
            }

        if file_url:
            fetch_url, google_ext, google_doc_id = _normalize_google_workspace_file_url(
                file_url
            )
            if google_ext is None and google_doc_id is None:
                drive_fetch = _normalize_google_drive_file_url(file_url)
                if drive_fetch:
                    fetch_url = drive_fetch
            dl_content, fname_hint, ct = await _fetch_url_bytes_capped(fetch_url)
            if google_ext:
                name_from_form = (doc_name or "").strip()
                cd_ok = Path(fname_hint).suffix.lower() in ALLOWED_EXTENSIONS
                if name_from_form:
                    fname_hint = (
                        f"{_safe_ingest_filename_stem(name_from_form)}{google_ext}"
                    )
                elif not cd_ok:
                    if google_doc_id:
                        fname_hint = f"{google_doc_id}{google_ext}"
                    else:
                        fname_hint = f"download{google_ext}"
            resolved_name = _resolve_ingest_filename(fname_hint, ct)
            ext = Path(resolved_name).suffix.lower()
            staged_path = await _save_pageindex_staging(
                "pageindex_ingest",
                agent_id,
                dl_content,
                resolved_name,
                {"source_url": file_url, "agent_id": agent_id},
            )
            content = dl_content
            filename = resolved_name
            doc_url_effective = (doc_url or "").strip() or file_url
        else:
            ext = Path(filename).suffix.lower()
            doc_url_effective = (doc_url or "").strip()

        if ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                f"Invalid file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        if not content:
            raise ValidationError("Empty file")

        effective_doc_name = doc_name or filename

        if normalize_bold_flag and not effective_forge:
            raise ValidationError(
                "normalize_bold_headings=yes requires JVAGENT_JVFORGE_BASE_URL "
                "(bold-line normalization runs on jvforge only)."
            )

        # Check if async mode is enabled
        async_mode = os.environ.get("JVAGENT_JVFORGE_ASYNC", "false").lower() == "true"

        try:
            if effective_forge:
                llm_wh_url = await _pageindex_llm_webhook_url_for_jvforge(agent_id)
                summary_for_forge = if_add_node_summary
                if summary_for_forge is None:
                    summary_for_forge = "yes" if get_pageindex_node_summary() else "no"

                if async_mode:
                    # Async mode: queue job and return immediately
                    result = await assimilate_via_jvforge_async(
                        base_url=effective_forge,
                        agent_id=agent_id,
                        doc_name=effective_doc_name,
                        model=model,
                        if_add_node_summary=summary_for_forge,
                        collection_name=collection_name,
                        metadata=metadata,
                        doc_description=doc_description,
                        doc_url=doc_url_effective or None,
                        convert_to_markdown=convert_to_markdown,
                        ocr=ocr_flag,
                        docling_ocr_engine=docling_ocr_engine_eff,
                        normalize_bold_headings=normalize_bold_flag,
                        llm_webhook_url=llm_wh_url,
                        emergency=False,  # Can be made configurable via form field
                        filename=filename,
                        content=content,
                    )

                    # Return async response with queue position (root_id/description
                    # only exist after processing; schema requires these keys)
                    return {
                        "status": result["status"],
                        "job_id": result["job_id"],
                        "queue_position": result["queue_position"],
                        "doc_name": result["doc_name"],
                        "message": result["message"],
                        "root_id": "",
                        "doc_description": None,
                        "chunks": 0,
                    }
                else:
                    # Sync mode: wait for processing to complete
                    result = await assimilate_via_jvforge(
                        base_url=effective_forge,
                        agent_id=agent_id,
                        doc_name=effective_doc_name,
                        model=model,
                        if_add_node_summary=summary_for_forge,
                        collection_name=collection_name,
                        metadata=metadata,
                        doc_description=doc_description,
                        doc_url=doc_url_effective or None,
                        convert_to_markdown=convert_to_markdown,
                        ocr=ocr_flag,
                        docling_ocr_engine=docling_ocr_engine_eff,
                        normalize_bold_headings=normalize_bold_flag,
                        llm_webhook_url=llm_wh_url,
                        filename=filename,
                        content=content,
                    )
            else:
                result = await _do_assimilate(
                    content,
                    ext,
                    doc_name=effective_doc_name,
                    model=model,
                    if_add_node_summary=if_add_node_summary,
                    collection_name=collection_name,
                    metadata=metadata,
                    doc_description=doc_description,
                    doc_url=doc_url_effective or None,
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr_flag,
                    docling_ocr_engine=docling_ocr_engine_eff,
                )
        except ImportError as e:
            raise ValidationError(str(e))
        except ValueError as e:
            raise ValidationError(str(e))

        doc_name_out = result.get("doc_name", "")
        chunks_out = (
            await count_document_chunks(doc_name_out, collection_name)
            if doc_name_out
            else 0
        )
        return {
            "doc_name": doc_name_out,
            "root_id": result.get("_root_id", ""),
            "doc_description": result.get("doc_description"),
            "chunks": chunks_out,
        }
    finally:
        await _delete_staged_file(staged_path)


@endpoint(
    "/agents/{agent_id}/pageindex/documents",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "documents": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Documents with doc_name, doc_description, root_id, collection_name, metadata, chunks",
                example=[
                    {
                        "doc_name": "my_doc",
                        "doc_description": "Description",
                        "root_id": "n.DocumentRootNode.abc123",
                        "collection_name": "example_agent",
                        "metadata": {"topic": "finance"},
                        "chunks": 42,
                    }
                ],
            ),
        }
    ),
)
async def list_documents_endpoint(
    agent_id: str,
    metadata: Optional[str] = Query(
        default=None, description='Metadata filter as JSON, e.g. {"topic": "finance"}'
    ),
) -> Dict[str, Any]:
    """List documents in the agent's PageIndex collection.

    **Query Parameters:**

    | Param | Type | Description |
    |-------|------|-------------|
    | metadata | string | Optional JSON object to filter by document metadata (AND semantics) |

    **Response:** `documents` — array of `{doc_name, doc_description, root_id, collection_name, metadata, chunks}`

    Collection is determined by `agent_id` from the path.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    metadata_filter = _parse_metadata(metadata)
    documents = await list_documents(
        collection_name=agent_id,
        metadata_filter=metadata_filter,
    )
    return {"documents": documents}


@endpoint(
    "/agents/{agent_id}/pageindex/chunks",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "chunks": ResponseField(
                field_type=List[Dict[str, Any]],
                description="All DocumentNode chunks in the agent collection",
            ),
            "total": ResponseField(
                field_type=int,
                description="Total chunks matching filter (before pagination cap)",
            ),
        }
    ),
)
async def list_collection_chunks_endpoint(
    agent_id: str,
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(
        default=0,
        ge=0,
        le=CHUNK_LIST_MAX,
        description=f"Chunks per page; 0 = all (capped at {CHUNK_LIST_MAX})",
    ),
    q: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring filter on title, text, summary, structure",
    ),
    chunk_enabled: Optional[str] = Query(
        default=None,
        description='Omit for all chunks; "true"/"enabled" = RAG-enabled only; '
        '"false"/"disabled" = disabled only',
    ),
) -> Dict[str, Any]:
    """List chunks across all documents in the agent's PageIndex collection."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    return await list_collection_chunks(
        collection_name=agent_id,
        page=page,
        per_page=per_page,
        q=q,
        enabled_filter=_parse_chunk_enabled_filter(chunk_enabled),
    )


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "doc_name": ResponseField(
                field_type=str, description="Document identifier"
            ),
            "doc_description": ResponseField(
                field_type=Optional[str],
                description="Document description",
            ),
            "root_id": ResponseField(
                field_type=str, description="Document root node ID"
            ),
            "metadata": ResponseField(
                field_type=Optional[Dict[str, Any]],
                description="Document-level metadata",
            ),
            "collection_name": ResponseField(
                field_type=str,
                description="Collection (typically agent_id)",
            ),
            "chunks": ResponseField(
                field_type=int,
                description="Number of DocumentNode chunks for this document",
            ),
        }
    ),
)
async def get_document_endpoint(agent_id: str, doc_name: str) -> Dict[str, Any]:
    """Get document metadata by name.

    **Path Parameters:**

    | Param | Description |
    |-------|-------------|
    | agent_id | Agent identifier (collection scope) |
    | doc_name | Document identifier |

    **Response:** `doc_name`, `doc_description`, `root_id`, `chunks`

    Returns 404 if the document is not found in the agent's collection.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=agent_id)
    if not root:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    chunks = await count_document_chunks(doc_name, agent_id)
    return {
        "doc_name": root.doc_name,
        "doc_description": root.doc_description,
        "root_id": root.id,
        "metadata": root.metadata,
        "collection_name": root.collection_name,
        "chunks": chunks,
    }


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}",
    methods=["PATCH"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "doc_name": ResponseField(
                field_type=str, description="Document identifier"
            ),
            "root_id": ResponseField(
                field_type=str, description="Document root node ID"
            ),
            "metadata": ResponseField(
                field_type=Optional[Dict[str, Any]],
                description="Updated metadata",
            ),
            "doc_url": ResponseField(
                field_type=Optional[str],
                description="Source URL for citations (document root)",
            ),
        }
    ),
)
async def patch_document_endpoint(
    agent_id: str,
    doc_name: str,
    updates: Dict[str, Any] = EndpointField(
        description='Include "metadata" (object or null) and/or "doc_url" (string or null)'
    ),
) -> Dict[str, Any]:
    """Update document root metadata and/or source URL (doc_url)."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    if not isinstance(updates, dict):
        raise ValidationError("Request body must be a JSON object")
    has_meta = "metadata" in updates
    has_url = "doc_url" in updates
    if not has_meta and not has_url:
        raise ValidationError('updates must include "metadata" and/or "doc_url"')
    fields: Dict[str, Any] = {}
    if has_meta:
        meta = updates["metadata"]
        if meta is not None and not isinstance(meta, dict):
            raise ValidationError("metadata must be a JSON object or null")
        fields["metadata"] = meta
    if has_url:
        u = updates["doc_url"]
        if u is not None and not isinstance(u, str):
            raise ValidationError("doc_url must be a string or null")
        fields["doc_url"] = u
    result = await patch_document_root(
        doc_name, collection_name=agent_id, fields=fields
    )
    if not result:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return result


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Document deleted",
            ),
        }
    ),
)
async def delete_document_endpoint(agent_id: str, doc_name: str) -> Dict[str, Any]:
    """Delete a document and all its nodes from the agent's PageIndex collection.

    **Path Parameters:**

    | Param | Description |
    |-------|-------------|
    | agent_id | Agent identifier (collection scope) |
    | doc_name | Document identifier to delete |

    **Response:** `message` — success confirmation

    Returns 404 if the document is not found in the agent's collection.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    deleted = await delete_document(doc_name, collection_name=agent_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return {"message": "Document deleted"}


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}/chunks",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "chunks": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Document section nodes (chunks)",
            ),
            "total": ResponseField(
                field_type=int,
                description="Total chunks matching filter (before pagination cap)",
            ),
        }
    ),
)
async def list_document_chunks_endpoint(
    agent_id: str,
    doc_name: str,
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(
        default=0,
        ge=0,
        le=CHUNK_LIST_MAX,
        description=f"Chunks per page; 0 = all (capped at {CHUNK_LIST_MAX})",
    ),
    q: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring filter on title, text, summary, structure",
    ),
    chunk_enabled: Optional[str] = Query(
        default=None,
        description='Omit for all chunks; "true"/"enabled" = RAG-enabled only; '
        '"false"/"disabled" = disabled only',
    ),
) -> Dict[str, Any]:
    """List chunks (DocumentNode) for a document with optional filter and pagination."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    if not await get_document_root(doc_name, collection_name=agent_id):
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return await list_document_chunks(
        doc_name,
        collection_name=agent_id,
        page=page,
        per_page=per_page,
        q=q,
        enabled_filter=_parse_chunk_enabled_filter(chunk_enabled),
    )


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}/chunks/{chunk_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "chunk": ResponseField(
                field_type=Dict[str, Any],
                description="Chunk fields",
            ),
        }
    ),
)
async def get_document_chunk_endpoint(
    agent_id: str,
    doc_name: str,
    chunk_id: str,
) -> Dict[str, Any]:
    """Get a single chunk by graph node id."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    chunk = await get_document_chunk(chunk_id, doc_name, collection_name=agent_id)
    if not chunk:
        raise ResourceNotFoundError(
            message="Chunk not found",
            details={"doc_name": doc_name, "chunk_id": chunk_id},
        )
    return {"chunk": chunk}


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}/chunks/{chunk_id}",
    methods=["PATCH"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "chunk": ResponseField(
                field_type=Dict[str, Any],
                description="Updated chunk",
            ),
        }
    ),
)
async def update_document_chunk_endpoint(
    agent_id: str,
    doc_name: str,
    chunk_id: str,
    updates: Dict[str, Any] = EndpointField(
        description="Partial fields: title, text, summary, prefix_summary, structure, "
        "node_id, start_index, end_index, physical_index, line_num, enabled, content_type"
    ),
) -> Dict[str, Any]:
    """Update chunk fields and refresh lexical index for this node."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    if not isinstance(updates, dict):
        raise ValidationError("Request body must be a JSON object")
    chunk = await update_document_chunk(
        chunk_id, doc_name, collection_name=agent_id, updates=updates
    )
    if not chunk:
        raise ResourceNotFoundError(
            message="Chunk not found",
            details={"doc_name": doc_name, "chunk_id": chunk_id},
        )
    return {"chunk": chunk}


@endpoint(
    "/agents/{agent_id}/pageindex/documents/{doc_name}/chunks/{chunk_id}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
            ),
        }
    ),
)
async def delete_document_chunk_endpoint(
    agent_id: str,
    doc_name: str,
    chunk_id: str,
    cascade: bool = Query(
        default=True,
        description="If true, delete this node and descendants in the document tree",
    ),
) -> Dict[str, Any]:
    """Delete a chunk; by default removes the subtree for tree-structured documents."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    ok = await delete_document_chunk(
        chunk_id,
        doc_name,
        collection_name=agent_id,
        cascade=cascade,
    )
    if not ok:
        raise ResourceNotFoundError(
            message="Chunk not found",
            details={"doc_name": doc_name, "chunk_id": chunk_id},
        )
    return {"message": "Chunk deleted"}


@endpoint(
    "/agents/{agent_id}/pageindex/documents/search",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "results": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Search results with content and document metadata",
                example=[
                    {
                        "node_id": "n.DocumentNode.xyz",
                        "title": "Section Title",
                        "doc_name": "my_doc",
                        "content": "Excerpt...",
                        "start_index": 5,
                        "end_index": 8,
                        "doc_url": "https://example.com/doc.pdf",
                    }
                ],
            ),
        }
    ),
)
async def search_documents_endpoint(
    agent_id: str,
    query: str = Field(..., description="Search query text"),
    doc_name: Optional[str] = Field(
        None, description="Scope search to a single document"
    ),
    strategy: str = Field(
        default="tree_search",
        description="Strategy: `tree_search` (LLM reasoning, recommended), `direct` (regex), or `walker` (graph traversal)",
    ),
    limit: int = Field(
        default=10, ge=1, le=200, description="Maximum number of results to return"
    ),
    metadata: Optional[str] = Field(
        None, description='Metadata filter as JSON, e.g. {"topic": "finance"}'
    ),
    include_references: bool = Field(
        default=True,
        description="When True, include doc_url on each hit (for citations). When False, omit doc_url.",
    ),
    only_enabled: bool = Field(
        default=True,
        description="When True, omit chunks with enabled=false from retrieval",
    ),
    include: Optional[List[str]] = Field(
        default=None,
        description="Extra metadata keys per hit: hierarchy, content_type, "
        "pageindex_node_id, line_num, etc.",
    ),
) -> Dict[str, Any]:
    """Search documents in the agent's PageIndex collection using vectorless retrieval.

    **Request Body (JSON):**

    | Field | Type | Required | Description |
    |-------|------|----------|-------------|
    | query | string | Yes | Search query text |
    | doc_name | string | No | Limit search to a single document |
    | strategy | string | No | `tree_search` (default), `direct`, or `walker` |
    | limit | integer | No | Max results (default: 10, max: 200) |
    | metadata | string | No | JSON object to filter by document metadata |
    | include_references | bool | No | When true (default), resolve doc_url per hit; when false, omit |
    | only_enabled | bool | No | When true (default), skip disabled chunks |
    | include | string[] | No | Extra fields per hit (e.g. hierarchy, content_type, pageindex_node_id) |

    **Response:** `results` — array of `{node_id, title, doc_name, content, text, summary, start_index, end_index, physical_index, doc_url}`

    Collection is determined by `agent_id` from the path.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    metadata_filter = _parse_metadata(metadata)
    results = await search_documents(
        query=query,
        doc_name=doc_name,
        strategy=strategy,
        limit=limit,
        collection_name=agent_id,
        metadata_filter=metadata_filter,
        include_references=include_references,
        only_enabled=only_enabled,
        include=include,
    )
    return {"results": results}


@endpoint(
    "/agents/{agent_id}/pageindex/export",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "data": ResponseField(
                field_type=dict,
                description="Exported graph data (roots, nodes, edges)",
            ),
        }
    ),
)
async def export_documents_endpoint(
    agent_id: str,
    doc_name: Optional[str] = Query(
        default=None, description="Optional document name to export single document"
    ),
    root_id: Optional[str] = Query(
        default=None,
        description="Optional DocumentRootNode id (e.g. n.DocumentRootNode.{uuid}). "
        "Exports that document only; takes precedence over doc_name. Omit both to export the entire collection.",
    ),
    export_format: str = Query(
        default="json", description="Export format: json or yaml"
    ),
) -> Dict[str, Any]:
    """Export PageIndex graph data.

    Omit ``doc_name`` and ``root_id`` to export all documents in the agent collection.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    data = await export_documents(
        collection_name=agent_id, doc_name=doc_name, root_id=root_id
    )

    if export_format.lower() == "yaml":
        try:
            import yaml

            data_str = yaml.dump(data, default_flow_style=False)
            return {"data": data_str, "format": "yaml"}
        except ImportError:
            logger.warning("PyYAML not available, falling back to JSON")

    return {"data": data, "format": "json"}


@endpoint(
    "/agents/{agent_id}/pageindex/import",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Import result message",
            ),
        }
    ),
)
async def import_documents_endpoint(
    agent_id: str,
    data: Any = EndpointField(
        default=None,
        description="Graph data (JSON object or YAML string). Omit when using import_url.",
    ),
    import_url: Optional[str] = EndpointField(
        default=None,
        description="URL of a JSON or YAML PageIndex export; server downloads, imports, then deletes the staged file.",
    ),
    purge: bool = EndpointField(
        default=False, description="Purge existing documents before import"
    ),
) -> Dict[str, str]:
    """Import PageIndex graph data from inline body or remote URL."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    url = (import_url or "").strip()
    staged_path: Optional[str] = None
    try:
        if url:
            if data is not None:
                raise ValidationError("Provide either import_url or data, not both")
            await _import_graph_from_remote_url(
                agent_id, url, purge=purge, staging_subdir="pageindex_import"
            )
        else:
            if data is None:
                raise ValidationError("Provide data or import_url")
            parsed = _coerce_import_payload_to_dict(data)
            await _run_import_parsed(agent_id, parsed, purge)

        return {"message": "Documents imported successfully"}

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error importing documents: {e}")
        raise ValidationError(f"Import failed: {str(e)}")
    finally:
        await _delete_staged_file(staged_path)


@endpoint(
    "/pageindex_retrieval_interact_action/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["PageIndex"],
    summary="PageIndex LLM webhook",
    description=(
        "Two modes (mutually exclusive): "
        "(1) **LLM completion** — JSON with **prompt** (required) and optional **model** "
        "(jvforge PageIndex LLM bridge). "
        "(2) **Graph import** — optional **process_document_url** (https URL of a PageIndex "
        "export JSON/YAML, e.g. jvforge job ``artifact_url``). Downloads and imports like "
        "``POST /api/agents/{agent_id}/pageindex/import`` with ``import_url``; optional **purge** "
        "(bool). Do not combine ``process_document_url`` with ``prompt``. "
        "Import responses are not valid LLM completions — do not send import payloads through "
        "the jvforge LLM webhook client. "
        "Authenticate with **api_key** query parameter or header."
    ),
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "result": ResponseField(
                field_type=Dict[str, Any],
                description=(
                    "LLM mode: text and model. Import mode: imported flag and message."
                ),
                example={"text": "Example reply", "model": "gpt-4o-mini"},
            ),
        }
    ),
)
async def pageindex_llm_webhook(request: Request, agent_id: str) -> Dict[str, Any]:
    """Inbound webhook: jvforge LLM completion or process-document graph import."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    core_action = await agent.get_action_by_type("PageIndexAction")
    if not core_action or not isinstance(core_action, PageIndexAction):
        raise ResourceNotFoundError(
            message="PageIndexAction not found for this agent",
            details={"agent_id": agent_id},
        )
    data = await request.json()
    if not isinstance(data, dict):
        data = await request.body()

    process_url = (data.get("process_document_url") or "").strip()
    if process_url:
        if (data.get("prompt") or "").strip():
            raise ValidationError(
                message="Cannot combine process_document_url with prompt",
                details={"agent_id": agent_id},
            )
        purge_flag = data.get("purge")
        purge = bool(purge_flag) if purge_flag is not None else False
        # AUDIT-actions Wave D: a leaked api_key + ``purge=True`` would
        # otherwise wipe the agent's index in one call. Require an
        # explicit ``confirm_purge`` field that echoes the agent_id, so
        # the caller has to know more than just the api_key.
        if purge:
            confirm = (data.get("confirm_purge") or "").strip()
            if confirm != agent_id:
                raise ValidationError(
                    message=(
                        "purge=True requires confirm_purge to match agent_id "
                        "(defense against API-key-only destructive calls)"
                    ),
                    details={"agent_id": agent_id},
                )
        initialize_pageindex_database(app_id=await _get_app_id_from_node())
        try:
            staged_path = await _stage_graph_from_remote_url(
                agent_id,
                process_url,
                staging_subdir="pageindex_webhook_import",
                fetch_read_timeout=900.0,
            )
        except ValidationError:
            raise
        except Exception as e:
            logger.error(
                "PageIndex process_document_url download/stage failed: %s",
                e,
                exc_info=True,
            )
            raise ValidationError(
                message=f"process_document_url import failed: {e}",
                details={"agent_id": agent_id},
            )
        _schedule_background_webhook_graph_import(
            agent_id,
            staged_path,
            purge=purge,
            process_url=process_url,
        )
        return {
            "status": "received",
            "result": {
                "imported": True,
                "message": (
                    "Artifact received; import is running in the background for large graphs."
                ),
            },
        }
    else:
        try:
            result = await core_action.handle_webhook_payload(data)
            return {"status": "received", "result": result}
        except ValidationError:
            raise
        except Exception as e:
            logger.error("PageIndex LLM webhook failed: %s", e, exc_info=True)
            raise ValidationError(
                message=f"LLM webhook failed: {e}",
                details={"agent_id": agent_id},
            )


@endpoint(
    "/agents/{agent_id}/pageindex/documents_queue",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "jobs": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Documents queue",
                example=[
                    {
                        "job_id": "123",
                        "doc_name": "Document 1",
                        "status": "queued",
                    }
                ],
            ),
            "total": ResponseField(
                field_type=int,
                description="Total number of documents in the queue",
                example=100,
            ),
        }
    ),
)
async def get_documents_queue_endpoint(
    agent_id: str,
) -> Dict[str, Any]:
    """Get the documents queue for the agent (proxied from jvforge ``/v1/queue``).

    When jvforge is not configured, native ingest has no remote queue; returns empty ``jobs``.

    Args:
        agent_id: Agent id.

    Returns:
        The documents queue for the agent.
    """
    forge_base = (get_jvagent_jvforge_base_url() or "").strip().rstrip("/")
    if not forge_base:
        return {"jobs": [], "total": 0}
    url = f"{forge_base}/v1/queue?agent_id={agent_id}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url)
    response.raise_for_status()
    body = response.json()
    jobs = body.get("jobs", []) if isinstance(body, dict) else []
    total = int(body.get("total", len(jobs))) if isinstance(body, dict) else len(jobs)
    return {"jobs": jobs, "total": total}


async def _jvforge_verify_queue_job_agent(agent_id: str, job_id: str) -> str:
    """Resolve jvforge base URL and confirm ``GET /v1/jobs/{job_id}`` belongs to ``agent_id``.

    Returns:
        Stripped forge base URL for further httpx calls.
    """
    forge_base = (get_jvagent_jvforge_base_url() or "").strip().rstrip("/")
    if not forge_base:
        raise ValidationError(
            message=(
                "Remote processing queue is unavailable: set JVAGENT_JVFORGE_BASE_URL "
                "to use jvforge queue operations (native ingest has no remote queue)."
            ),
            details={"agent_id": agent_id},
        )
    safe_jid = quote(job_id, safe="")
    job_get_url = f"{forge_base}/v1/jobs/{safe_jid}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        get_resp = await client.get(job_get_url)
    if get_resp.status_code == 404:
        raise ResourceNotFoundError(
            message="Job not found",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    get_resp.raise_for_status()
    meta = get_resp.json()
    if not isinstance(meta, dict):
        raise ResourceNotFoundError(
            message="Job not found",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    job_agent = (meta.get("agent_id") or "").strip()
    if job_agent != agent_id:
        raise ResourceNotFoundError(
            message="Job not found",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    return forge_base


@endpoint(
    "/agents/{agent_id}/pageindex/documents_queue/{job_id}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "job_id": ResponseField(
                field_type=str,
                description="jvforge job id",
            ),
            "status": ResponseField(
                field_type=str,
                description="Expected `cancelled` after delete",
            ),
            "message": ResponseField(
                field_type=str,
                description="Human-readable status",
            ),
        }
    ),
)
async def cancel_documents_queue_job_endpoint(
    agent_id: str,
    job_id: str,
) -> Dict[str, Any]:
    """Remove a processing-queue job (proxied to jvforge ``DELETE /v1/jobs/{job_id}``).

    Verifies the job belongs to ``agent_id`` before forwarding.
    """
    forge_base = await _jvforge_verify_queue_job_agent(agent_id, job_id)
    safe_jid = quote(job_id, safe="")
    delete_url = f"{forge_base}/v1/jobs/{safe_jid}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        del_resp = await client.delete(delete_url)
    if del_resp.status_code == 404:
        raise ResourceNotFoundError(
            message="Job not found in queue",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    if del_resp.status_code >= 400:
        err_payload = del_resp.json() if del_resp.content else {}
        detail: Any = (
            err_payload.get("detail")
            if isinstance(err_payload, dict)
            else del_resp.text
        )
        if isinstance(detail, list) and detail:
            detail = detail[0]
        msg = str(detail) if detail else "Cannot cancel job"
        raise ValidationError(
            message=msg,
            details={"job_id": job_id, "agent_id": agent_id},
        )
    body = del_resp.json()
    if not isinstance(body, dict):
        raise ValidationError(
            message="Unexpected response from jvforge",
            details={"agent_id": agent_id},
        )
    return body


@endpoint(
    "/agents/{agent_id}/pageindex/documents_queue/{job_id}/boost",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "job_id": ResponseField(
                field_type=str,
                description="jvforge job id",
            ),
            "status": ResponseField(
                field_type=str,
                description="Expected `boosted`",
            ),
            "queue_position": ResponseField(
                field_type=Dict[str, Any],
                description="Overall and per-agent queue position",
            ),
            "message": ResponseField(
                field_type=str,
                description="Human-readable status",
            ),
            "status_url": ResponseField(
                field_type=str,
                description="jvforge job status URL",
            ),
        }
    ),
)
async def boost_documents_queue_job_endpoint(
    agent_id: str,
    job_id: str,
) -> Dict[str, Any]:
    """Move a queued job to the front (proxied to jvforge ``POST /v1/jobs/{job_id}/boost``).

    Verifies the job belongs to ``agent_id`` before forwarding.
    """
    forge_base = await _jvforge_verify_queue_job_agent(agent_id, job_id)
    safe_jid = quote(job_id, safe="")
    boost_url = f"{forge_base}/v1/jobs/{safe_jid}/boost"
    async with httpx.AsyncClient(timeout=120.0) as client:
        post_resp = await client.post(boost_url)
    if post_resp.status_code == 404:
        raise ResourceNotFoundError(
            message="Job not found",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    if post_resp.status_code == 400:
        err_payload = post_resp.json()
        detail: Any = (
            err_payload.get("detail")
            if isinstance(err_payload, dict)
            else post_resp.text
        )
        if isinstance(detail, list) and detail:
            detail = detail[0]
        msg = str(detail) if detail else "Cannot boost job"
        raise ValidationError(
            message=msg,
            details={"job_id": job_id, "agent_id": agent_id},
        )
    post_resp.raise_for_status()
    body = post_resp.json()
    if not isinstance(body, dict):
        raise ValidationError(
            message="Unexpected response from jvforge",
            details={"agent_id": agent_id},
        )
    return body


@endpoint(
    "/agents/{agent_id}/pageindex/documents_queue/{job_id}/retry",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "job_id": ResponseField(
                field_type=str,
                description="jvforge job id",
            ),
            "status": ResponseField(
                field_type=str,
                description="Expected `queued` after retry",
            ),
            "queue_position": ResponseField(
                field_type=Dict[str, Any],
                description="Overall and per-agent queue position",
            ),
            "message": ResponseField(
                field_type=str,
                description="Human-readable status",
            ),
            "status_url": ResponseField(
                field_type=str,
                description="jvforge job status URL",
            ),
        }
    ),
)
async def retry_documents_queue_job_endpoint(
    agent_id: str,
    job_id: str,
) -> Dict[str, Any]:
    """Re-queue a failed processing job (proxied to jvforge ``POST /v1/jobs/{job_id}/retry``).

    Verifies the job belongs to ``agent_id`` before forwarding.
    """
    forge_base = await _jvforge_verify_queue_job_agent(agent_id, job_id)
    safe_jid = quote(job_id, safe="")
    retry_url = f"{forge_base}/v1/jobs/{safe_jid}/retry"
    async with httpx.AsyncClient(timeout=120.0) as client:
        post_resp = await client.post(retry_url)
    if post_resp.status_code == 404:
        raise ResourceNotFoundError(
            message="Job not found",
            details={"job_id": job_id, "agent_id": agent_id},
        )
    if post_resp.status_code == 400:
        err_payload = post_resp.json()
        detail: Any = (
            err_payload.get("detail")
            if isinstance(err_payload, dict)
            else post_resp.text
        )
        if isinstance(detail, list) and detail:
            detail = detail[0]
        msg = str(detail) if detail else "Cannot retry job"
        raise ValidationError(
            message=msg,
            details={"job_id": job_id, "agent_id": agent_id},
        )
    post_resp.raise_for_status()
    body = post_resp.json()
    if not isinstance(body, dict):
        raise ValidationError(
            message="Unexpected response from jvforge",
            details={"agent_id": agent_id},
        )
    return body
