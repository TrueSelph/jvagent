"""Export an approved Google Doc directly to PDF."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from jvagent.skills.fileinterface.scripts._core import copy_host_file_into_sandbox
from jvagent.skills.pdf_generation.scripts._document_args import (
    default_drive_filename,
    parse_document_pdf_arguments,
)
from jvagent.skills.pdf_generation.scripts._sandbox_output import (
    resolve_sandbox_pdf_output_dir,
    sandbox_pdf_dest_relpath,
)


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pdf_generation__export_google_doc_pdf",
        "description": (
            "Export a Google Doc to PDF and save it in the sandbox output directory. "
            "Use this as the primary path when final approval happened in Google Docs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Google Docs document ID to export.",
                },
                "title": {
                    "type": "string",
                    "description": "Final document title used for output naming.",
                },
                "subtitle": {"type": "string"},
                "output_basename": {"type": "string"},
                "output_dir": {
                    "type": "string",
                    "description": "Sandbox-relative output directory (default output).",
                },
            },
            "required": ["document_id", "title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"success": False, "error": "ActionResolver not available"}
    action = await resolver.resolve("GoogleDocsAction")
    if action is None:
        return {
            "success": False,
            "error": "GoogleDocsAction not found. Use latex/weasy fallback tools.",
        }

    document_id = arguments.get("document_id")
    params = parse_document_pdf_arguments(arguments)

    pdf_bytes = await action.export_pdf(document_id=document_id)
    if not pdf_bytes:
        return {"success": False, "error": "Google Docs export returned empty content"}

    try:
        sandbox_dir = resolve_sandbox_pdf_output_dir(arguments, visitor)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    temp_path = Path(tempfile.gettempdir()) / default_drive_filename(params)
    temp_path.write_bytes(pdf_bytes)
    dest_relpath = sandbox_pdf_dest_relpath(sandbox_dir, params)
    await copy_host_file_into_sandbox(visitor, str(temp_path), dest_relpath)
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "success": True,
        "method": "google_docs_export",
        "document_id": document_id,
        "pdf_path": dest_relpath,
    }
