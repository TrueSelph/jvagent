"""Shared parameter normalization for PDF generation tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


def _slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip())
    s = s.strip("_") or "document"
    return s[:max_len].rstrip("_") or "document"


@dataclass(frozen=True)
class DocumentPdfParams:
    """Canonical inputs for Markdown/plain-text → PDF rendering."""

    title: str
    content: str
    subtitle: str
    author: str
    date: str
    mark_confidential: bool
    drive_output_folder_id: Optional[str]
    output_basename: Optional[str]
    prepared_for_label: str
    presented_by_label: str


def parse_document_pdf_arguments(raw: Dict[str, Any]) -> DocumentPdfParams:
    """Resolve generic and legacy keys to a single shape.

    Generic keys: title, content, subtitle, author, date, mark_confidential,
    output_basename, prepared_for_label, drive_output_folder_id.

    Legacy: body (alias for content), client_name (alias for subtitle),
    company_name (alias for author).
    """
    title = (raw.get("title") or "Document").strip()
    content = raw.get("content") or raw.get("body") or ""
    if isinstance(content, str):
        content = content.strip()
    else:
        content = str(content)
    subtitle = (raw.get("subtitle") or raw.get("client_name") or "").strip()
    author = (raw.get("author") or raw.get("company_name") or "").strip()
    date_str = (raw.get("date") or "").strip()
    if not date_str:
        date_str = datetime.now().strftime("%B %d, %Y")

    mc = raw.get("mark_confidential", True)
    if isinstance(mc, str):
        mark_confidential = mc.strip().lower() in ("1", "true", "yes", "on")
    else:
        mark_confidential = bool(mc)

    ob = raw.get("output_basename")
    if ob is not None and str(ob).strip():
        output_basename = str(ob).strip()
    else:
        output_basename = None

    pfl = (raw.get("prepared_for_label") or "Prepared for").strip() or "Prepared for"
    prbl = (raw.get("presented_by_label") or "Presented by").strip() or "Presented by"

    drive = raw.get("drive_output_folder_id")
    if drive is not None and str(drive).strip():
        drive_id: Optional[str] = str(drive).strip()
    else:
        drive_id = None

    return DocumentPdfParams(
        title=title,
        content=content,
        subtitle=subtitle,
        author=author,
        date=date_str,
        mark_confidential=mark_confidential,
        drive_output_folder_id=drive_id,
        output_basename=output_basename,
        prepared_for_label=pfl,
        presented_by_label=prbl,
    )


def default_drive_filename(params: DocumentPdfParams) -> str:
    """Default upload filename when output_basename is not provided."""
    if params.output_basename:
        name = params.output_basename
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        return name
    stem = _slug(params.subtitle) if params.subtitle else _slug(params.title)
    d = datetime.now().strftime("%Y%m%d")
    return f"Document_{stem}_{d}.pdf"
