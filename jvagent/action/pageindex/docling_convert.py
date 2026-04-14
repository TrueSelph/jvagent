"""Docling-based conversion of documents (primarily PDF) to Markdown with page markers.

Optional dependency: declared in ``jvagent/action/pageindex/info.yaml`` (pip install on action load), or
``pip install 'jvagent[pageindex]'`` / ``pip install docling tabulate`` for manual installs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

_DOC_NOT_INSTALLED_MSG = (
    "Docling is required for convert_to_markdown=True. "
    "Use an agent with jvagent/pageindex_retrieval_interact_action (see jvagent/action/pageindex/info.yaml), "
    "or: pip install 'jvagent[pageindex]' or pip install docling tabulate"
)


def _docling_items_to_markdown(document: object) -> str:
    """Serialize DoclingDocument items to markdown with ``--- [ Page N ] ---`` breaks."""
    from docling_core.types.doc.document import (
        ListItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )

    parts: List[str] = []
    current_page = 0

    for item, _level in document.iterate_items():  # type: ignore[union-attr]
        page_no: Optional[int] = None
        prov = getattr(item, "prov", None)
        if prov:
            try:
                page_no = prov[0].page_no
            except (IndexError, AttributeError):
                page_no = None
        if page_no is not None and page_no > current_page:
            current_page = page_no
            parts.append("")
            parts.append(f"--- [ Page {current_page} ] ---")
            parts.append("")

        if isinstance(item, TableItem):
            try:
                parts.append(item.export_to_markdown(doc=document))
            except Exception:
                logger.debug(
                    "Table export failed; falling back to grid export", exc_info=True
                )
                parts.append(item.export_to_markdown(doc=None))
            parts.append("")
        elif isinstance(item, SectionHeaderItem):
            lvl = int(getattr(item, "level", 1) or 1)
            hashes = "#" * max(1, min(lvl, 6))
            parts.append(f"{hashes} {item.text}")
            parts.append("")
        elif isinstance(item, TitleItem):
            parts.append(f"# {item.text}")
            parts.append("")
        elif isinstance(item, ListItem):
            t = (item.text or "").strip()
            prefix = f"{item.marker} " if getattr(item, "marker", None) else "- "
            if t:
                parts.append(f"{prefix}{t}")
                parts.append("")
        elif isinstance(item, TextItem):
            t = (item.text or "").strip()
            if t:
                parts.append(item.text)
                parts.append("")
        else:
            # Pictures and other node types: try document markdown serializer
            try:
                from docling_core.transforms.serializer.markdown import (
                    MarkdownDocSerializer,
                    MarkdownParams,
                )
                from docling_core.types.doc.document import ImageRefMode

                serializer = MarkdownDocSerializer(
                    doc=document,
                    params=MarkdownParams(image_mode=ImageRefMode.PLACEHOLDER),
                )
                chunk = serializer.serialize(item=item).text
                if chunk and chunk.strip():
                    parts.append(chunk.strip())
                    parts.append("")
            except Exception:
                logger.debug("Skipped docling item in markdown export", exc_info=True)

    return "\n".join(parts).strip() + "\n"


def convert_document_to_markdown_sync(
    source_path: Union[str, Path],
    *,
    ocr: bool = False,
) -> str:
    """Convert a file to markdown using Docling (PDF pipeline uses OCR/tables per options).

    Args:
        source_path: Path to a PDF or other Docling-supported format.
        ocr: Enable OCR in the PDF pipeline (scanned pages).

    Returns:
        Markdown string with ``--- [ Page N ] ---`` markers at page transitions.
    """
    path = Path(source_path)
    if not path.is_file():
        raise FileNotFoundError(f"Docling source not found: {path}")

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as e:
        raise ImportError(_DOC_NOT_INSTALLED_MSG) from e

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = ocr
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    else:
        # Office / HTML / images: default Docling routing; OCR flag N/A
        converter = DocumentConverter()

    result = converter.convert(str(path))
    if not result or not result.document:
        raise RuntimeError("Docling conversion produced no document")

    return _docling_items_to_markdown(result.document)
