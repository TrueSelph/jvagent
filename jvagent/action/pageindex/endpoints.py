"""PageIndex document ingestion and management endpoints.

Vectorless RAG: ingest PDF/Markdown documents, list, search, and delete.
All routes are agent-scoped (collection = agent_id from path).
"""

import json
import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Query, Request
from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from pydantic import Field
from python_multipart.multipart import FormParser, parse_options_header

from .documents import (
    assimilate_document,
    delete_document,
    export_documents,
    get_document_root,
    import_documents,
    list_documents,
)
from .pageindex_retrieval_interact_action import ensure_ingestion_config_for_agent
from .retrieval import search_documents

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown"}


def _parse_metadata(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse metadata JSON string. Returns None if empty or invalid."""
    if not value or not value.strip():
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_multipart_safe(body: bytes, content_type: str) -> tuple[
    bytes,
    str,
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[str],
]:
    """Parse multipart form-data from raw body without decoding file content.

    Returns (file_content, filename, doc_name, model, if_add_node_summary, collection_name, metadata, doc_description).
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

    def _safe_str(b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")

    def on_field(field) -> None:
        nonlocal doc_name, model, if_add_node_summary, collection_name, metadata_raw, doc_description
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

    def on_file(f) -> None:
        nonlocal file_content, filename
        filename = _safe_str(f.file_name) if f.file_name else ""
        f.file_object.seek(0)
        file_content = f.file_object.read()
        # Do NOT call f.close() - FormParser may finalize the writer again in _on_end

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
) -> Dict[str, Any]:
    """Run assimilate_document on content. Handles PDF vs Markdown and temp files."""
    assimilate_kw = {
        "doc_name": doc_name,
        "model": model,
        "if_add_node_summary": if_add_node_summary,
        "collection_name": collection_name,
        "metadata": metadata,
        "doc_description": doc_description,
    }

    if ext == ".pdf":
        doc = BytesIO(content)
        try:
            return await assimilate_document(doc, **assimilate_kw)
        except UnicodeDecodeError:
            pass
        ext = ".md"

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=ext,
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        return await assimilate_document(tmp_path, **assimilate_kw)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


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
    """Ingest a PDF or Markdown document into the agent's PageIndex collection.

    **Request:** `multipart/form-data`

    | Field | Type | Required | Description |
    |-------|------|----------|-------------|
    | file | File | Yes | PDF or Markdown file (`.pdf`, `.md`, `.markdown`) |
    | doc_name | string | No | Override document identifier (default: derived from filename) |
    | doc_description | string | No | Human-readable document description |
    | if_add_node_summary | string | No | "yes" or "no" – generate LLM summaries per node (default: from agent's PageIndex config) |
    | metadata | string | No | JSON object for tagging, e.g. `{"topic": "finance", "year": 2024}` |

    **Response:** `doc_name`, `root_id`, `doc_description`

    Documents are stored in the agent's collection (collection = `agent_id` from path).
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise ValidationError("Expected multipart/form-data")

    body = await request.body()
    (
        content,
        filename,
        doc_name,
        model,
        if_add_node_summary,
        collection_name,
        metadata_raw,
        doc_description,
    ) = _parse_multipart_safe(body, content_type)
    collection_name = collection_name or agent_id
    metadata = _parse_metadata(metadata_raw)

    if if_add_node_summary is None:
        await ensure_ingestion_config_for_agent(agent_id)

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    if not content:
        raise ValidationError("Empty file")

    try:
        result = await _do_assimilate(
            content,
            ext,
            doc_name=doc_name,
            model=model,
            if_add_node_summary=if_add_node_summary,
            collection_name=collection_name,
            metadata=metadata,
            doc_description=doc_description,
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
    metadata_filter = _parse_metadata(metadata)
    documents = await list_documents(
        collection_name=agent_id,
        metadata_filter=metadata_filter,
    )
    return {"documents": documents}


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
    }


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
    deleted = await delete_document(doc_name, collection_name=agent_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return {"message": "Document deleted"}


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
                description="Search results",
                example=[
                    {
                        "node_id": "n.DocumentNode.xyz",
                        "title": "Section Title",
                        "doc_name": "my_doc",
                        "content": "Excerpt...",
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

    **Response:** `results` — array of `{node_id, title, doc_name, content, text, summary}`

    Collection is determined by `agent_id` from the path.
    """
    metadata_filter = _parse_metadata(metadata)
    results = await search_documents(
        query=query,
        doc_name=doc_name,
        strategy=strategy,
        limit=limit,
        collection_name=agent_id,
        metadata_filter=metadata_filter,
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
    format: str = Query(default="json", description="Export format: json or yaml"),
) -> Dict[str, Any]:
    """Export PageIndex graph data."""
    data = await export_documents(collection_name=agent_id, doc_name=doc_name)

    if format.lower() == "yaml":
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
    data: Any = EndpointField(description="Graph data (JSON object or YAML string)"),
    purge: bool = EndpointField(
        default=False, description="Purge existing documents before import"
    ),
) -> Dict[str, str]:
    """Import PageIndex graph data."""
    try:
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

        await import_documents(parsed, purge=purge, collection_name=agent_id)

        return {"message": "Documents imported successfully"}

    except Exception as e:
        logger.error(f"Error importing documents: {e}")
        raise ValidationError(f"Import failed: {str(e)}")
