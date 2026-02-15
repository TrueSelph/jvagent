"""PageIndex document ingestion and management endpoints."""

import logging
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request

from pydantic import Field

from python_multipart.multipart import FormParser, parse_options_header

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .documents import (
    assimilate_document,
    delete_document,
    get_document_root,
    list_documents,
)
from .retrieval import search_documents

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown"}



def _parse_multipart_safe(body: bytes, content_type: str) -> tuple[bytes, str, Optional[str], Optional[str], Optional[str]]:
    """Parse multipart form-data from raw body without decoding file content.

    Returns (file_content, filename, doc_name, model, if_add_node_summary). Uses latin-1 for headers
    to avoid UTF-8 decode errors on non-ASCII filenames or field values.
    """
    content_type_bytes = content_type.encode("latin-1") if isinstance(content_type, str) else content_type
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

    def _safe_str(b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")

    def on_field(field) -> None:
        nonlocal doc_name, model, if_add_node_summary
        name = _safe_str(field.field_name) if field.field_name else ""
        val = field.value
        value = _safe_str(val) if val is not None else ""
        if name == "doc_name":
            doc_name = value or None
        elif name == "model":
            model = value or None
        elif name == "if_add_node_summary":
            if_add_node_summary = value or None

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
    return file_content, filename, doc_name, model, if_add_node_summary


@endpoint(
    "/pageindex/documents",
    methods=["POST"],
    auth=True,
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
async def ingest_document_endpoint(request: Request) -> Dict[str, Any]:
    """Ingest a PDF or Markdown document into the PageIndex graph.

    Accepts multipart form data with a file and optional doc_name override.
    Supported formats: .pdf, .md, .markdown
    Parses raw body to avoid UTF-8 decode errors on non-UTF-8 file content.
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise ValidationError("Expected multipart/form-data")

    body = await request.body()
    content, filename, doc_name, model, if_add_node_summary = _parse_multipart_safe(body, content_type)

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    if not content:
        raise ValidationError("Empty file")

    async def _assimilate_markdown(content_bytes: bytes, suffix: str, node_summary: Optional[str] = None) -> Dict[str, Any]:
        """Decode bytes as UTF-8 (with replacement) and assimilate as markdown."""
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = content_bytes.decode("utf-8", errors="replace")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=suffix,
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        try:
            return await assimilate_document(tmp_path, doc_name=doc_name, model=model, if_add_node_summary=node_summary)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    try:
        if ext == ".pdf":
            doc = BytesIO(content)
            try:
                result = await assimilate_document(
                    doc,
                    doc_name=doc_name,
                    model=model,
                    if_add_node_summary=if_add_node_summary,
                )
            except UnicodeDecodeError:
                # PDF parser failed on encoding; retry as markdown (handles misnamed .pdf that is actually text)
                result = await _assimilate_markdown(content, ".md", if_add_node_summary)
        else:
            # Markdown: decode with error handling for non-UTF-8 files (e.g. Latin-1)
            try:
                text = content.decode("utf-8")
                used_replace = False
            except UnicodeDecodeError:
                text = content.decode("utf-8", errors="replace")
                used_replace = True
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=ext,
                delete=False,
            ) as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                result = await assimilate_document(
                    tmp_path,
                    doc_name=doc_name,
                    model=model,
                    if_add_node_summary=if_add_node_summary,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        return {
            "doc_name": result.get("doc_name", ""),
            "root_id": result.get("_root_id", ""),
            "doc_description": result.get("doc_description"),
        }
    except UnicodeDecodeError:
        raise
    except ImportError as e:
        raise ValidationError(str(e))
    except ValueError as e:
        raise ValidationError(str(e))
    except Exception:
        raise


@endpoint(
    "/pageindex/documents",
    methods=["GET"],
    auth=True,
    tags=["PageIndex"],
    response=success_response(
        data={
            "documents": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of documents",
                example=[
                    {
                        "doc_name": "my_doc",
                        "doc_description": "Description",
                        "root_id": "n.DocumentRootNode.abc123",
                    }
                ],
            ),
        }
    ),
)
async def list_documents_endpoint() -> Dict[str, Any]:
    """List all documents in the PageIndex graph."""
    documents = await list_documents()
    return {"documents": documents}


@endpoint(
    "/pageindex/documents/{doc_name}",
    methods=["GET"],
    auth=True,
    tags=["PageIndex"],
    response=success_response(
        data={
            "doc_name": ResponseField(
                field_type=str,
                description="Document identifier",
            ),
            "doc_description": ResponseField(
                field_type=Optional[str],
                description="Document description",
            ),
            "root_id": ResponseField(
                field_type=str,
                description="Document root node ID",
            ),
        }
    ),
)
async def get_document_endpoint(doc_name: str) -> Dict[str, Any]:
    """Get document metadata by name."""
    root = await get_document_root(doc_name)
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
    "/pageindex/documents/{doc_name}",
    methods=["DELETE"],
    auth=True,
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
async def delete_document_endpoint(doc_name: str) -> Dict[str, Any]:
    """Delete a document and all its nodes from the PageIndex graph."""
    deleted = await delete_document(doc_name)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Document '{doc_name}' not found",
            details={"doc_name": doc_name},
        )
    return {"message": "Document deleted"}


@endpoint(
    "/pageindex/documents/search",
    methods=["POST"],
    auth=True,
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
    query: str = Field(..., description="Search query"),
    doc_name: Optional[str] = Field(None, description="Optional document name to scope search"),
    strategy: str = Field(
        default="tree_search",
        description="Search strategy: 'tree_search', 'direct', or 'walker'",
    ),
    limit: int = Field(default=20, ge=1, le=200, description="Max results to return"),
) -> Dict[str, Any]:
    """Search documents using vectorless retrieval (tree_search, direct, or walker strategy)."""
    results = await search_documents(
        query=query,
        doc_name=doc_name,
        strategy=strategy,
        limit=limit,
    )
    return {"results": results}
