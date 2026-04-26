"""Ingest a document into PageIndex via ActionResolver."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "assimilate",
        "description": "Ingest a document (PDF, Markdown, or text) into the PageIndex index.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "string",
                    "description": (
                        "Document to ingest: HTTPS URL, absolute host path, or path relative "
                        "to the current user's jvspatial sandbox (preferred for user uploads). "
                        "Relative paths are read from sandbox storage first; if missing, the "
                        "path is resolved on the local filesystem (legacy)."
                    ),
                },
                "doc_name": {
                    "type": "string",
                    "description": "Name for the document (default: derived from file name)",
                },
                "collection_name": {
                    "type": "string",
                    "description": "Collection to ingest into (default: agent's collection)",
                },
                "metadata": {
                    "type": "object",
                    "description": "Custom key-value metadata for filtering at query time",
                },
                "doc_description": {
                    "type": "string",
                    "description": "Description of the document",
                },
                "doc_url": {
                    "type": "string",
                    "description": "Source URL of the document resource",
                },
                "convert_to_markdown": {
                    "type": "boolean",
                    "description": "Convert PDF to Markdown via Docling before indexing (default: false)",
                },
                "ocr": {
                    "type": "boolean",
                    "description": "Enable OCR for scanned PDF pages when using Docling (default: false)",
                },
            },
            "required": ["doc"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Any:
    """Ingest a document by delegating to PageIndexAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("PageIndexAction")
    if action is None:
        return {"error": "PageIndexAction not found on this agent"}

    raw_doc = arguments["doc"]
    doc: Any = raw_doc
    doc_name = arguments.get("doc_name")

    if isinstance(raw_doc, str):
        doc_s = raw_doc.strip()
        low = doc_s.lower()
        if low.startswith(("http://", "https://")):
            doc = doc_s
        elif os.path.isabs(doc_s):
            doc = doc_s
        else:
            try:
                from jvagent.skills.fileinterface import _core

                data = await _core.read_binary_file(visitor, doc_s)
                if data is not None:
                    doc = data
                    if not doc_name:
                        doc_name = Path(doc_s.replace("\\", "/")).name
                else:
                    doc = doc_s
            except Exception as e:
                logger.debug("assimilate: sandbox read skipped for %r: %s", doc_s, e)
                doc = doc_s

    return await action.assimilate(
        doc=doc,
        doc_name=doc_name,
        collection_name=arguments.get("collection_name"),
        metadata=arguments.get("metadata"),
        doc_description=arguments.get("doc_description"),
        doc_url=arguments.get("doc_url"),
        convert_to_markdown=arguments.get("convert_to_markdown", False),
        ocr=arguments.get("ocr", False),
    )
