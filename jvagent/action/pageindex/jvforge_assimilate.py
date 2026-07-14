"""POST documents to jvforge /v1/process (sync) or /v1/jobs (async) and persist pageindex_graph locally."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from typing import Any, Dict, Mapping, Optional, Tuple, cast

import httpx
from jvspatial.api.exceptions import ValidationError

from .adapter import strip_redundant_md_suffix
from .documents import delete_document, get_document_root, import_documents

logger = logging.getLogger(__name__)

_JVFORGE_POST_ATTEMPTS = 5
_JVFORGE_RETRY_BACKOFF_S = (2.0, 4.0, 8.0, 16.0)

# Transient transport failures worth retrying (connection drops mid-upload/response).
_TRANSIENT_HTTPX = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


async def _post_jvforge_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str],
    data: Optional[Mapping[str, str]] = None,
    files: Optional[Any] = None,
) -> httpx.Response:
    """POST to jvforge, retrying transient httpx transport errors and 499 status.

    499 (Client Closed Request) is returned by jvforge when the client disconnects
    mid-upload; callers that retry on transport errors should also retry on 499.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _JVFORGE_POST_ATTEMPTS + 1):
        try:
            kwargs: Dict[str, Any] = {"headers": dict(headers)}
            if files is not None and data is not None:
                kwargs["data"] = data
                kwargs["files"] = files
            elif files is not None:
                kwargs["files"] = files
            else:
                kwargs["data"] = data
            r = await client.post(url, **kwargs)
            if r.status_code == 499:
                if attempt >= _JVFORGE_POST_ATTEMPTS:
                    break
                delay = _JVFORGE_RETRY_BACKOFF_S[
                    min(attempt - 1, len(_JVFORGE_RETRY_BACKOFF_S) - 1)
                ]
                logger.warning(
                    "jvforge POST %s returned 499 (client disconnect); retry %d/%d in %.1fs",
                    url,
                    attempt,
                    _JVFORGE_POST_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            return r
        except _TRANSIENT_HTTPX as exc:
            last_exc = exc
            if attempt >= _JVFORGE_POST_ATTEMPTS:
                break
            delay = _JVFORGE_RETRY_BACKOFF_S[
                min(attempt - 1, len(_JVFORGE_RETRY_BACKOFF_S) - 1)
            ]
            logger.warning(
                "jvforge POST %s failed (%s: %s); retry %d/%d in %.1fs",
                url,
                type(exc).__name__,
                exc,
                attempt,
                _JVFORGE_POST_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)

    if last_exc is not None:
        raise ValidationError(
            message=(
                f"jvforge request failed (connection error): {last_exc}. "
                "Check JVAGENT_JVFORGE_BASE_URL and that jvforge is running."
            ),
            details={
                "error_type": type(last_exc).__name__ if last_exc else None,
                "url": url,
                "attempts": _JVFORGE_POST_ATTEMPTS,
            },
        )
    raise ValidationError(
        message="jvforge request failed: server repeatedly closed connection (499)",
        details={"url": url, "attempts": _JVFORGE_POST_ATTEMPTS},
    )


def _rewrite_pageindex_graph_doc_names(
    graph: Dict[str, Any], raw_name: str, normalized: str
) -> None:
    """Align roots/nodes doc_name with normalized value before ``import_documents``."""
    if raw_name == normalized:
        return
    for root in graph.get("roots") or []:
        if isinstance(root, dict) and root.get("doc_name") == raw_name:
            root["doc_name"] = normalized
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("doc_name") == raw_name:
            node["doc_name"] = normalized


def _jvforge_form_data(
    *,
    agent_id: str,
    doc_name: str,
    model: Optional[str],
    if_add_node_summary: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
    doc_description: Optional[str],
    doc_url: Optional[str],
    convert_to_markdown: bool,
    ocr: bool,
    docling_ocr_engine: Optional[str] = None,
    normalize_bold_headings: bool,
    llm_webhook_url: str,
    file_url: Optional[str] = None,
) -> Dict[str, str]:
    data: Dict[str, str] = {
        "agent_id": agent_id,
        "llm_webhook_url": llm_webhook_url,
        "collection_name": collection_name,
        "doc_name": doc_name,
        "convert_to_markdown": "yes" if convert_to_markdown else "no",
        "ocr": "yes" if ocr else "no",
        "normalize_bold_headings": "yes" if normalize_bold_headings else "no",
    }
    if docling_ocr_engine:
        data["docling_ocr_engine"] = docling_ocr_engine
    if model:
        data["model"] = model
    data["if_add_node_summary"] = if_add_node_summary
    if doc_description:
        data["doc_description"] = doc_description
    if doc_url:
        data["doc_url"] = doc_url
    if metadata:
        data["metadata"] = json.dumps(metadata)
    if file_url:
        data["file_url"] = file_url
    return data


def _eff_doc_name_from_graph(graph: Dict[str, Any], fallback: str) -> str:
    roots = graph.get("roots") or []
    if roots and isinstance(roots[0], dict):
        dn = roots[0].get("doc_name")
        if dn is not None and str(dn).strip():
            return str(dn).strip()
    fb = (fallback or "").strip()
    if fb:
        return fb
    raise ValidationError(
        "jvforge graph missing doc_name",
        details={"keys": list(graph.keys())},
    )


async def assimilate_via_jvforge(
    *,
    base_url: str,
    agent_id: str,
    doc_name: str,
    model: Optional[str],
    if_add_node_summary: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
    doc_description: Optional[str],
    doc_url: Optional[str],
    convert_to_markdown: bool,
    ocr: bool,
    docling_ocr_engine: Optional[str] = None,
    normalize_bold_headings: bool = False,
    llm_webhook_url: str,
    filename: Optional[str] = None,
    content: Optional[bytes] = None,
    file_url: Optional[str] = None,
) -> Dict[str, Any]:
    """POST document bytes or ``file_url`` to jvforge /v1/process, then persist the pageindex_graph locally.

    Provide either ``file_url`` (remote ingest on jvforge) or ``filename`` + ``content`` (upload), not both.
    """
    fu = (file_url or "").strip()
    has_bytes = content is not None and len(content) > 0
    if fu and has_bytes:
        raise ValidationError(
            "Provide either file_url or file content for jvforge, not both"
        )
    if not fu and (not has_bytes or not (filename or "").strip()):
        raise ValidationError(
            "filename and file content are required when file_url is not set"
        )

    url = f"{base_url.strip().rstrip('/')}/v1/process?response_format=pageindex_graph"
    data = _jvforge_form_data(
        agent_id=agent_id,
        doc_name=doc_name,
        model=model,
        if_add_node_summary=if_add_node_summary,
        collection_name=collection_name,
        metadata=metadata,
        doc_description=doc_description,
        doc_url=doc_url,
        convert_to_markdown=convert_to_markdown,
        ocr=ocr,
        docling_ocr_engine=docling_ocr_engine,
        normalize_bold_headings=normalize_bold_headings,
        llm_webhook_url=llm_webhook_url,
        file_url=fu or None,
    )

    headers: Dict[str, str] = {}
    api_key = (
        os.environ.get("JVAGENT_JVFORGE_API_KEY")
        or os.environ.get("JVFORGE_API_KEY")
        or ""
    ).strip()
    if api_key:
        headers["X-API-Key"] = api_key

    timeout = httpx.Timeout(600.0, connect=60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if fu:
            multipart = {k: cast(Tuple[None, str], (None, v)) for k, v in data.items()}
            r = await _post_jvforge_with_retries(
                client, url, headers=headers, files=multipart
            )
        else:
            fn = (filename or "").strip()
            ctype, _ = mimetypes.guess_type(fn)
            if not ctype:
                ctype = "application/octet-stream"
            files = {"file": (fn, content or b"", ctype)}
            r = await _post_jvforge_with_retries(
                client, url, headers=headers, data=data, files=files
            )

    if r.status_code >= 400:
        detail = (r.text or "")[:2000]
        try:
            body = r.json()
            if isinstance(body, dict) and "detail" in body:
                detail = str(body["detail"])
        except Exception:
            pass
        raise ValidationError(
            message=f"jvforge processing failed ({r.status_code}): {detail}",
            details={"status_code": r.status_code},
        )

    graph = r.json()
    if not isinstance(graph, dict):
        raise ValidationError(
            "jvforge returned invalid response",
            details={"response_type": type(graph).__name__},
        )

    # Schema sanity-check: a successful jvforge ingest MUST return at least one
    # root (DocumentRootNode). Without this check a malformed response (e.g.
    # missing or empty ``roots``) silently produces a zero-document import that
    # downstream code reports as success — masking a real upstream failure.
    raw_roots = graph.get("roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ValidationError(
            "jvforge response missing 'roots' (no document produced)",
            details={
                "roots_type": type(raw_roots).__name__,
                "keys": sorted(graph.keys())[:20],
            },
        )

    eff_doc_name = _eff_doc_name_from_graph(graph, doc_name)
    normalized = strip_redundant_md_suffix(eff_doc_name)
    _rewrite_pageindex_graph_doc_names(graph, eff_doc_name, normalized)
    eff_doc_name = normalized

    roots = graph.get("roots") or []
    root_id = ""
    if roots and isinstance(roots[0], dict):
        root_id = str(roots[0].get("id") or "")
    doc_description_out: Optional[str] = None
    if roots and isinstance(roots[0], dict):
        raw_desc = roots[0].get("doc_description")
        if raw_desc is not None:
            doc_description_out = str(raw_desc) if raw_desc else None

    await delete_document(eff_doc_name, collection_name=collection_name)
    await import_documents(graph, purge=False, collection_name=collection_name)

    if not root_id:
        got = await get_document_root(eff_doc_name, collection_name=collection_name)
        if got:
            root_id = str(got.id or "")

    return {
        "doc_name": eff_doc_name,
        "_root_id": root_id,
        "doc_description": doc_description_out,
    }


async def assimilate_via_jvforge_async(
    *,
    base_url: str,
    agent_id: str,
    doc_name: str,
    model: Optional[str],
    if_add_node_summary: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
    doc_description: Optional[str],
    doc_url: Optional[str],
    convert_to_markdown: bool,
    ocr: bool,
    docling_ocr_engine: Optional[str] = None,
    normalize_bold_headings: bool = False,
    llm_webhook_url: str,
    emergency: bool = False,
    filename: Optional[str] = None,
    content: Optional[bytes] = None,
    file_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST document to jvforge /v1/jobs (async), return immediately with job info.

    Provide either ``file_url`` or ``filename`` + ``content``.

    Returns:
        Dict with status, job_id, queue_position, doc_name, message
    """
    fu = (file_url or "").strip()
    has_bytes = content is not None and len(content) > 0
    if fu and has_bytes:
        raise ValidationError(
            "Provide either file_url or file content for jvforge, not both"
        )
    if not fu and (not has_bytes or not (filename or "").strip()):
        raise ValidationError(
            "filename and file content are required when file_url is not set"
        )

    base = f"{base_url.strip().rstrip('/')}/v1/jobs"
    url = f"{base}?emergency=true" if emergency else base
    data = _jvforge_form_data(
        agent_id=agent_id,
        doc_name=doc_name,
        model=model,
        if_add_node_summary=if_add_node_summary,
        collection_name=collection_name,
        metadata=metadata,
        doc_description=doc_description,
        doc_url=doc_url,
        convert_to_markdown=convert_to_markdown,
        ocr=ocr,
        docling_ocr_engine=docling_ocr_engine,
        normalize_bold_headings=normalize_bold_headings,
        llm_webhook_url=llm_webhook_url,
        file_url=fu or None,
    )

    headers: Dict[str, str] = {}
    api_key = (
        os.environ.get("JVAGENT_JVFORGE_API_KEY")
        or os.environ.get("JVFORGE_API_KEY")
        or ""
    ).strip()
    if api_key:
        headers["X-API-Key"] = api_key

    # Same order of magnitude as assimilate_via_jvforge: large multipart uploads can exceed
    # tens of seconds before jvforge reads the body and returns 202 (short async handler).
    async_http_timeout_s = float(
        (os.environ.get("JVAGENT_JVFORGE_ASYNC_HTTP_TIMEOUT") or "600").strip() or "600"
    )
    timeout = httpx.Timeout(async_http_timeout_s, connect=60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if fu:
            multipart = {k: cast(Tuple[None, str], (None, v)) for k, v in data.items()}
            r = await _post_jvforge_with_retries(
                client, url, headers=headers, files=multipart
            )
        else:
            fn = (filename or "").strip()
            ctype, _ = mimetypes.guess_type(fn)
            if not ctype:
                ctype = "application/octet-stream"
            files = {"file": (fn, content or b"", ctype)}
            r = await _post_jvforge_with_retries(
                client, url, headers=headers, data=data, files=files
            )

    if r.status_code >= 400:
        detail = (r.text or "")[:2000]
        try:
            body = r.json()
            if isinstance(body, dict) and "detail" in body:
                detail = str(body["detail"])
        except Exception:
            pass
        raise ValidationError(
            message=f"jvforge queueing failed ({r.status_code}): {detail}",
            details={"status_code": r.status_code},
        )

    body = r.json()
    bdict = body if isinstance(body, dict) else {}
    queued_doc = str(bdict.get("doc_name") or "").strip()

    # Handle both 200 (duplicate) and 202 (queued) responses
    if r.status_code == 200:
        # Document already in queue
        return {
            "status": "already_queued",
            "job_id": bdict.get("job_id"),
            "queue_position": bdict.get(
                "queue_position", {"overall": 0, "per_agent": 0}
            ),
            "doc_name": queued_doc
            or (doc_name or "").strip()
            or (filename or "").strip(),
            "message": bdict.get("message", "Document already in queue"),
        }
    elif r.status_code == 202:
        # Successfully queued
        return {
            "status": "queued",
            "job_id": bdict.get("job_id"),
            "queue_position": bdict.get(
                "queue_position", {"overall": 0, "per_agent": 0}
            ),
            "doc_name": queued_doc
            or (doc_name or "").strip()
            or (filename or "").strip(),
            "message": "Document queued for processing",
        }
    else:
        raise ValidationError(
            message=f"Unexpected response status: {r.status_code}",
            details={"status_code": r.status_code},
        )
