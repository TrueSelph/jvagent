"""POST documents to jvforge /v1/process and persist pageindex_graph locally."""

from __future__ import annotations

import json
import mimetypes
import os
from typing import Any, Dict, Optional

import httpx
from jvspatial.api.exceptions import ValidationError

from .documents import delete_document, get_document_root, import_documents


async def assimilate_via_jvforge(
    *,
    base_url: str,
    agent_id: str,
    filename: str,
    content: bytes,
    doc_name: str,
    model: Optional[str],
    if_add_node_summary: str,
    collection_name: str,
    metadata: Optional[Dict[str, Any]],
    doc_description: Optional[str],
    doc_url: Optional[str],
    convert_to_markdown: bool,
    ocr: bool,
    llm_webhook_url: str,
) -> Dict[str, Any]:
    """POST document bytes to jvforge /v1/process, then persist the pageindex_graph locally."""
    url = f"{base_url.strip().rstrip('/')}/v1/process?response_format=pageindex_graph"
    data: Dict[str, str] = {
        "agent_id": agent_id,
        "llm_webhook_url": llm_webhook_url,
        "collection_name": collection_name,
        "doc_name": doc_name,
        "convert_to_markdown": "yes" if convert_to_markdown else "no",
        "ocr": "yes" if ocr else "no",
    }
    if model:
        data["model"] = model
    data["if_add_node_summary"] = if_add_node_summary
    if doc_description:
        data["doc_description"] = doc_description
    if doc_url:
        data["doc_url"] = doc_url
    if metadata:
        data["metadata"] = json.dumps(metadata)

    headers: Dict[str, str] = {}
    api_key = (
        (
            os.environ.get("JVAGENT_JVFORGE_API_KEY")
            or os.environ.get("JVFORGE_API_KEY")
            or ""
        ).strip()
    )
    if api_key:
        headers["X-API-Key"] = api_key

    ctype, _ = mimetypes.guess_type(filename)
    if not ctype:
        ctype = "application/octet-stream"
    files = {"file": (filename, content, ctype)}
    timeout = httpx.Timeout(600.0, connect=60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, data=data, files=files, headers=headers)

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

    roots = graph.get("roots") or []
    root_id = ""
    if roots and isinstance(roots[0], dict):
        root_id = str(roots[0].get("id") or "")
    doc_description_out: Optional[str] = None
    if roots and isinstance(roots[0], dict):
        raw_desc = roots[0].get("doc_description")
        if raw_desc is not None:
            doc_description_out = str(raw_desc) if raw_desc else None

    await delete_document(doc_name, collection_name=collection_name)
    await import_documents(graph, purge=False, collection_name=collection_name)

    if not root_id:
        got = await get_document_root(doc_name, collection_name=collection_name)
        if got:
            root_id = str(got.id or "")

    return {
        "doc_name": doc_name,
        "_root_id": root_id,
        "doc_description": doc_description_out,
    }
