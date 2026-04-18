"""PageIndex document ingestion and management endpoints.

Vectorless RAG: ingest PDF, Markdown/text, and office documents; list, search, delete,
export/import, and manage retrieval access via ``user_groups`` on
``PageIndexRetrievalInteractAction``.
All routes are agent-scoped (collection = agent_id from path unless noted).

``user_groups`` routes:

- GET ``/agents/{agent_id}/pageindex/user_groups`` — read map group → user ids.
- POST ``/agents/{agent_id}/pageindex/user_groups/members`` — body: ``group``, optional ``user_session``.
  With a non-empty ``user_session``, appends that id (deduped). With ``user_session`` omitted or blank, sets the
  group to an empty list (blank group).
- DELETE ``/agents/{agent_id}/pageindex/user_groups/members`` — remove a member from a group
  (query: ``group``, ``user_session``, optional ``can_delete_group``, default false). If the last
  member is removed, the group stays as ``[]`` unless ``can_delete_group`` is true, then the group
  key is removed.
- DELETE ``/agents/{agent_id}/pageindex/user_groups/groups`` — remove a group key
  (query: ``group``).
"""

import json
import logging
import mimetypes
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

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
    _ensure_pageindex_work_dir,
    assimilate_document,
    delete_document,
    delete_document_chunk,
    export_documents,
    get_document_chunk,
    get_document_root,
    import_documents,
    list_collection_chunks,
    list_document_chunks,
    list_documents,
    update_document_chunk,
    update_document_metadata,
)
from .jvforge_assimilate import assimilate_via_jvforge
from .pageindex_retrieval_interact_action import (
    PageIndexRetrievalInteractAction,
    ensure_ingestion_config_for_agent,
)
from .retrieval import search_documents

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (
    {".pdf"} | PAGEINDEX_TEXT_LIKE_EXTENSIONS | PAGEINDEX_OFFICE_LIKE_EXTENSIONS
)


def _strip_nonempty(label: str, value: Optional[str]) -> str:
    """Return stripped string or raise ValidationError if empty."""
    s = (value or "").strip()
    if not s:
        raise ValidationError(
            f"{label} is required",
            details={label: value},
        )
    return s


def _copy_user_groups_map(
    action: PageIndexRetrievalInteractAction,
) -> Dict[str, List[str]]:
    """Shallow copy of user_groups with list copies per group."""
    raw = getattr(action, "user_groups", {})
    return {str(k): list(v) for k, v in raw.items()}


async def _get_pageindex_retrieval_action(
    agent_id: str,
) -> PageIndexRetrievalInteractAction:
    """Load the agent's PageIndexRetrievalInteractAction or raise."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action = await agent.get_action_by_type("PageIndexRetrievalInteractAction")
    if not action or not isinstance(action, PageIndexRetrievalInteractAction):
        raise ResourceNotFoundError(
            message=(
                f"No PageIndexRetrievalInteractAction found for agent '{agent_id}'"
            ),
            details={"agent_id": agent_id},
        )
    return action


async def _get_app_id_from_node() -> Optional[str]:
    """Get app_id from App node. JVAGENT_APP_ID env overrides when set in config."""
    from jvagent.core.app import App

    app = await App.get()
    return getattr(app, "app_id", None) if app else None


MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


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


def _safe_pageindex_relative_path(*segments: str) -> str:
    rel = "/".join(segments)
    try:
        return PathSanitizer.sanitize_path(rel.replace("\\", "/"))
    except (InvalidPathError, PathTraversalError) as e:
        raise ValidationError(f"Invalid storage path: {e}")


async def _fetch_url_bytes_capped(url: str) -> Tuple[bytes, str, Optional[str]]:
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raise ValidationError("URL must be http or https")
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", raw) as resp:
            if resp.status_code != 200:
                raise ValidationError(f"Download failed: HTTP {resp.status_code}")
            ct_header = resp.headers.get("content-type")
            content_type: Optional[str] = None
            if ct_header:
                content_type = ct_header.split(";")[0].strip()
            cd = resp.headers.get("content-disposition")
            fname = _filename_from_content_disposition(cd) or _filename_from_url(raw)
            total = 0
            chunks: List[bytes] = []
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValidationError(
                        f"Remote file exceeds maximum size "
                        f"({MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
    if not content:
        raise ValidationError("Downloaded file is empty")
    return content, fname, content_type


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
        raw, _fname_hint, ct = await _fetch_url_bytes_capped(url)
        staging_fn = _import_staging_filename(url, ct)
        meta: Dict[str, Any] = {"source_url": url, "agent_id": agent_id}
        if extra_staging_metadata:
            meta = {**meta, **extra_staging_metadata}
        staged_path = await _save_pageindex_staging(
            staging_subdir,
            agent_id,
            raw,
            staging_fn,
            meta,
        )
        text = raw.decode("utf-8", errors="replace")
        parsed = _coerce_import_payload_to_dict(text)
        await _run_import_parsed(agent_id, parsed, purge)
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
]:
    """Parse multipart form-data from raw body without decoding file content.

    Returns (file_content, filename, doc_name, model, if_add_node_summary,
             collection_name, metadata, doc_description, doc_url,
             convert_to_markdown, ocr, file_url).
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
    file_url: Optional[str] = None

    def _safe_str(b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")

    def on_field(field) -> None:
        nonlocal doc_name, model, if_add_node_summary, collection_name, metadata_raw, doc_description, doc_url, convert_to_markdown, ocr, file_url
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
        elif name == "file_url":
            file_url = value or None

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
        file_url,
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
    | file_url | string | One of file / file_url | HTTPS/HTTP URL; server downloads, stages under `.files`, ingests, then deletes the staged file |
    | doc_name | string | No | Override document identifier (default: derived from filename) |
    | doc_description | string | No | Human-readable document description |
    | doc_url | string | No | Source URL for reference citations (default when using file_url: the same download URL) |
    | if_add_node_summary | string | No | "yes" or "no" – generate LLM summaries per node (default: from agent's PageIndex config) |
    | convert_to_markdown | string | No | "yes" or "no" – use Docling to convert PDF to Markdown first (default: no) |
    | ocr | string | No | "yes" or "no" – enable OCR when using Docling on PDF (default: no) |
    | metadata | string | No | JSON object for tagging, e.g. `{"topic": "finance", "year": 2024}` |

    **Response:** `doc_name`, `root_id`, `doc_description`

    Documents are stored in the agent's collection (collection = `agent_id` from path).

    When ``JVAGENT_JVFORGE_BASE_URL`` is set, ingestion is delegated to that jvforge service
    (``POST /v1/process``) with ``llm_webhook_url`` from the agent's PageIndex retrieval action;
    set ``JVAGENT_JVFORGE_API_KEY`` (or ``JVFORGE_API_KEY``) if jvforge requires ``X-API-Key``.
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
        file_url_raw,
    ) = _parse_multipart_safe(body, content_type)
    collection_name = collection_name or agent_id
    metadata = _parse_metadata(metadata_raw)

    if if_add_node_summary is None:
        await ensure_ingestion_config_for_agent(agent_id)

    convert_opt = _form_yes_no_optional(convert_to_markdown_raw)
    convert_to_markdown = False if convert_opt is None else convert_opt
    ocr_opt = _form_yes_no_optional(ocr_raw)
    ocr_flag = False if ocr_opt is None else ocr_opt

    file_url = (file_url_raw or "").strip()
    has_upload = len(content) > 0
    if file_url and has_upload:
        raise ValidationError("Provide either file or file_url, not both")
    if not file_url and not has_upload:
        raise ValidationError("Provide a file upload or file_url")

    staged_path: Optional[str] = None
    try:
        if file_url:
            dl_content, fname_hint, ct = await _fetch_url_bytes_capped(file_url)
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
        forge_base = get_jvagent_jvforge_base_url()
        try:
            if forge_base:
                retrieval_action = await _get_pageindex_retrieval_action(agent_id)
                llm_wh_url = await retrieval_action.get_webhook_url()
                summary_for_forge = if_add_node_summary
                if summary_for_forge is None:
                    summary_for_forge = "yes" if get_pageindex_node_summary() else "no"
                result = await assimilate_via_jvforge(
                    base_url=forge_base,
                    agent_id=agent_id,
                    filename=filename,
                    content=content,
                    doc_name=effective_doc_name,
                    model=model,
                    if_add_node_summary=summary_for_forge,
                    collection_name=collection_name,
                    metadata=metadata,
                    doc_description=doc_description,
                    doc_url=doc_url_effective or None,
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr_flag,
                    llm_webhook_url=llm_wh_url,
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
                )
        except ImportError as e:
            raise ValidationError(str(e))
        except ValueError as e:
            raise ValidationError(str(e))

        return {
            "doc_name": result.get("doc_name", ""),
            "root_id": result.get("_root_id", ""),
            "doc_description": result.get("doc_description"),
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
                description="Documents with doc_name, doc_description, root_id, collection_name, metadata",
                example=[
                    {
                        "doc_name": "my_doc",
                        "doc_description": "Description",
                        "root_id": "n.DocumentRootNode.abc123",
                        "collection_name": "example_agent",
                        "metadata": {"topic": "finance"},
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

    **Response:** `documents` — array of `{doc_name, doc_description, root_id, collection_name, metadata}`

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

    **Response:** `doc_name`, `doc_description`, `root_id`

    Returns 404 if the document is not found in the agent's collection.
    """
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    root = await get_document_root(doc_name, collection_name=agent_id)
    if not root:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return {
        "doc_name": root.doc_name,
        "doc_description": root.doc_description,
        "root_id": root.id,
        "metadata": root.metadata,
        "collection_name": root.collection_name,
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
        }
    ),
)
async def patch_document_endpoint(
    agent_id: str,
    doc_name: str,
    updates: Dict[str, Any] = EndpointField(
        description='Must include "metadata": object or null to set document root metadata'
    ),
) -> Dict[str, Any]:
    """Update document root fields (currently metadata only)."""
    initialize_pageindex_database(app_id=await _get_app_id_from_node())
    if not isinstance(updates, dict):
        raise ValidationError("Request body must be a JSON object")
    if "metadata" not in updates:
        raise ValidationError('updates must include "metadata"')
    meta = updates["metadata"]
    if meta is not None and not isinstance(meta, dict):
        raise ValidationError("metadata must be a JSON object or null")
    result = await update_document_metadata(
        doc_name, collection_name=agent_id, metadata=meta
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
    try:
        if url:
            if data is not None:
                raise ValidationError("Provide either import_url or data, not both")
            await _import_graph_from_remote_url(agent_id, url, purge=purge)
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


_USER_GROUPS_FIELD = ResponseField(
    field_type=Dict[str, Any],
    description="Map of access group name to list of user ids",
    example={"finance": ["usr_1", "usr_2"]},
)


@endpoint(
    "/agents/{agent_id}/pageindex/user_groups",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "user_groups": _USER_GROUPS_FIELD,
        }
    ),
)
async def get_user_groups_endpoint(agent_id: str) -> Dict[str, Any]:
    """Return ``user_groups`` for the agent's PageIndex retrieval action.

    Args:
        agent_id: Agent id (must have a PageIndexRetrievalInteractAction).

    Returns:
        Dict with key ``user_groups`` (empty dict if unset).

    Raises:
        ResourceNotFoundError: If the agent or PageIndex retrieval action is missing.
    """
    action = await _get_pageindex_retrieval_action(agent_id)
    return {"user_groups": _copy_user_groups_map(action)}


@endpoint(
    "/agents/{agent_id}/pageindex/user_groups/members",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "user_groups": _USER_GROUPS_FIELD,
            "message": ResponseField(
                field_type=str,
                description="Outcome message",
                example="User added to group",
            ),
        }
    ),
)
async def add_user_group_member_endpoint(
    agent_id: str,
    group: str = EndpointField(description="Access group name"),
    user_session: Optional[str] = EndpointField(
        default=None,
        description=(
            "User session id to add to the group (deduped). Omit or leave blank to set the group "
            "to an empty member list (blank group). Use ``user_session``, not "
            "``user_id`` (reserved for auth injection on authenticated routes)."
        ),
    ),
) -> Dict[str, Any]:
    """Add a member to ``group``, or clear the group to an empty list when ``user_session`` is absent.

    If ``user_session`` is non-empty after stripping, appends that id to the group (deduplicated).
    If ``user_session`` is omitted or blank, sets ``group`` to ``[]`` (other groups unchanged).

    Args:
        agent_id: Agent id.
        group: Group key in ``user_groups``.
        user_session: Member id to add, or empty/omitted for a blank group.

    Returns:
        Updated ``user_groups`` and a short message.

    Raises:
        ResourceNotFoundError: If the agent or PageIndex retrieval action is missing.
        ValidationError: If ``group`` is empty.
    """
    group_key = _strip_nonempty("group", group)
    action = await _get_pageindex_retrieval_action(agent_id)
    ug = _copy_user_groups_map(action)
    if user_session is not None and str(user_session).strip():
        uid = _strip_nonempty("user_session", user_session)
        members = list(ug.get(group_key, []))
        if uid in members:
            return {"user_groups": ug, "message": "User already in group"}
        members.append(uid)
        ug[group_key] = members
        action.user_groups = ug
        await action.save()
        return {"user_groups": ug, "message": "User added to group"}
    prior = list(ug.get(group_key, []))
    ug[group_key] = []
    if prior == []:
        return {"user_groups": ug, "message": "Group already blank"}
    action.user_groups = ug
    await action.save()
    return {"user_groups": ug, "message": "Group cleared to empty list"}


@endpoint(
    "/agents/{agent_id}/pageindex/user_groups/members",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "user_groups": _USER_GROUPS_FIELD,
            "message": ResponseField(
                field_type=str,
                description="Outcome message",
                example="User removed from group",
            ),
        }
    ),
)
async def remove_user_group_member_endpoint(
    agent_id: str,
    group: str = Query(..., description="Access group name"),
    user_session: str = Query(..., description="User session id to remove from the group"),
    can_delete_group: bool = Query(
        default=False,
        description="If true, remove the group key when the last member is removed; "
        "if false (default), keep the group as an empty list",
    ),
) -> Dict[str, Any]:
    """Remove ``user_session`` from ``group``.

    If that was the last member: by default the group is kept as ``[]``. When
    ``can_delete_group`` is true, the group key is deleted instead.

    Args:
        agent_id: Agent id.
        group: Group key in ``user_groups``.
        user_session: User session id to remove.
        can_delete_group: When true, drop the group key if it becomes empty after removal.

    Returns:
        Updated ``user_groups`` and a short message.

    Raises:
        ResourceNotFoundError: If the agent or PageIndex retrieval action is missing.
        ValidationError: If ``group`` or ``user_session`` is empty.
    """
    group_key = _strip_nonempty("group", group)
    uid = _strip_nonempty("user_session", user_session)
    action = await _get_pageindex_retrieval_action(agent_id)
    ug = _copy_user_groups_map(action)
    if group_key not in ug:
        return {"user_groups": ug, "message": "Group not present; nothing removed"}
    old_members = ug[group_key]
    filtered = [u for u in old_members if u != uid]
    if len(filtered) == len(old_members):
        return {"user_groups": ug, "message": "User not in group; nothing removed"}
    if filtered:
        ug[group_key] = filtered
        msg = "User removed from group"
    elif can_delete_group:
        del ug[group_key]
        msg = "User removed; empty group deleted"
    else:
        ug[group_key] = []
        msg = "User removed from group"
    action.user_groups = ug
    await action.save()
    return {"user_groups": ug, "message": msg}


@endpoint(
    "/agents/{agent_id}/pageindex/user_groups/groups",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["PageIndex"],
    response=success_response(
        data={
            "user_groups": _USER_GROUPS_FIELD,
            "message": ResponseField(
                field_type=str,
                description="Outcome message",
                example="Group removed",
            ),
        }
    ),
)
async def delete_user_group_endpoint(
    agent_id: str,
    group: str = Query(..., description="Access group name to remove entirely"),
) -> Dict[str, Any]:
    """Remove a group key and its member list from ``user_groups``.

    Args:
        agent_id: Agent id.
        group: Group key to delete.

    Returns:
        Updated ``user_groups`` and a short message.

    Raises:
        ResourceNotFoundError: If the agent or PageIndex retrieval action is missing.
        ValidationError: If ``group`` is empty.
    """
    group_key = _strip_nonempty("group", group)
    action = await _get_pageindex_retrieval_action(agent_id)
    ug = _copy_user_groups_map(action)
    if group_key in ug:
        del ug[group_key]
        action.user_groups = ug
        await action.save()
        return {"user_groups": ug, "message": "Group removed"}
    return {"user_groups": ug, "message": "Group not present; nothing removed"}


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
    """Inbound webhook: LLM call or process-document URL import for PageIndex retrieval."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action = await agent.get_action_by_type("PageIndexRetrievalInteractAction")
    if not action or not isinstance(action, PageIndexRetrievalInteractAction):
        raise ResourceNotFoundError(
            message="PageIndexRetrievalInteractAction not found for this agent",
            details={"agent_id": agent_id},
        )

    data = getattr(request.state, "parsed_payload", None)
    if data is None:
        try:
            body = await request.body()
            if body:
                data = await request.json()
            else:
                data = {}
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    process_url = (data.get("process_document_url") or "").strip()
    if process_url:
        if (data.get("prompt") or "").strip():
            raise ValidationError(
                message="Cannot combine process_document_url with prompt",
                details={"agent_id": agent_id},
            )
        purge_flag = data.get("purge")
        purge = bool(purge_flag) if purge_flag is not None else False
        initialize_pageindex_database(app_id=await _get_app_id_from_node())
        try:
            await _import_graph_from_remote_url(
                agent_id,
                process_url,
                purge=purge,
                staging_subdir="pageindex_webhook_import",
            )
        except ValidationError:
            raise
        except Exception as e:
            logger.error("PageIndex process_document_url import failed: %s", e, exc_info=True)
            raise ValidationError(
                message=f"process_document_url import failed: {e}",
                details={"agent_id": agent_id},
            )
        return {
            "status": "received",
            "result": {
                "imported": True,
                "message": "Documents imported successfully",
            },
        }

    try:
        result = await action.handle_webhook_payload(data)
        return {"status": "received", "result": result}
    except ValidationError:
        raise
    except Exception as e:
        logger.error("PageIndex LLM webhook failed: %s", e, exc_info=True)
        raise ValidationError(
            message=f"LLM webhook failed: {e}",
            details={"agent_id": agent_id},
        )
