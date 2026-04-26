"""Resolve sandbox-relative output paths for PDF artifacts (aligns with fileinterface)."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.skills.fileinterface._core import normalize_sandbox_dir_prefix
from jvagent.skills.pdf_generation._document_args import (
    DocumentPdfParams,
    default_drive_filename,
)


def resolve_sandbox_pdf_output_dir(arguments: Dict[str, Any], visitor: Any) -> str:
    """Directory prefix under the user sandbox (tool arg, action config, or ``output``)."""
    action = getattr(visitor, "_current_action", None)
    configured = getattr(action, "output_dir", None) if action else None
    return normalize_sandbox_dir_prefix(
        arguments.get("output_dir") or configured,
        default="output",
    )


def sandbox_pdf_dest_relpath(sandbox_dir: str, params: DocumentPdfParams) -> str:
    """Sandbox-relative path ``{dir}/{filename}.pdf`` for the final artifact."""
    return f"{sandbox_dir}/{default_drive_filename(params)}"
