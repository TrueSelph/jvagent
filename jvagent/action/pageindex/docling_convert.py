"""Docling-based conversion of documents (primarily PDF) to Markdown with page markers.

Optional dependency: declared in ``jvagent/action/pageindex/info.yaml`` (pip install on action load), or
``pip install 'jvagent[pageindex]'`` / ``pip install docling tabulate`` for manual installs.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_DOC_NOT_INSTALLED_MSG = (
    "Docling is required for convert_to_markdown=True. "
    "Use an agent with jvagent/pageindex_retrieval_interact_action (see jvagent/action/pageindex/info.yaml), "
    "or: pip install 'jvagent[pageindex]' or pip install docling tabulate"
)

# Aligned with jvforge ``pi_vendor.docling_convert.DOCLING_IMAGE_EXTENSIONS``.
_DOCLING_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
)


def wants_ooxml_pdf_for_docling_ocr(
    *, ocr: bool, docling_ocr_engine: Optional[str]
) -> bool:
    """Aligned with jvforge ``office_convert.wants_ooxml_to_pdf_for_docling_ocr``."""
    if ocr:
        return True
    raw = (docling_ocr_engine or "").strip().lower()
    return bool(raw) and raw not in ("none", "off", "no", "false", "0")


_OOXML_OCR_SUFFIXES = frozenset({".docx", ".pptx"})


def _suffix_uses_standard_pdf_pipeline(suffix: str) -> bool:
    s = suffix.lower()
    return s == ".pdf" or s in _DOCLING_IMAGE_EXTENSIONS


def _ooxml_to_pdf_via_libreoffice(path: Path, *, timeout: float = 120.0) -> Tuple[Path, Path]:
    """Convert OOXML to PDF; returns ``(pdf_path, tmp_dir)`` for cleanup."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not on PATH")
    tmp_root = Path(tempfile.mkdtemp(prefix="jvagent_lo_"))
    outdir = tmp_root
    outdir.mkdir(parents=True, exist_ok=True)
    src = path.resolve()
    cmd = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--invisible",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir.resolve()),
        str(src),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(err)
    expected = outdir / f"{path.stem}.pdf"
    if not expected.is_file():
        for p in outdir.iterdir():
            if p.suffix.lower() == ".pdf" and p.stem == path.stem:
                return p, tmp_root
        raise RuntimeError(f"LibreOffice did not create {expected.name}")
    return expected, tmp_root


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
    docling_ocr_engine: Optional[str] = None,
) -> str:
    """Convert a file to markdown using Docling (PDF pipeline uses OCR/tables per options).

    Args:
        source_path: Path to a PDF or other Docling-supported format.
        ocr: Enable OCR in the PDF pipeline (scanned pages).
        docling_ocr_engine: Combined with ``wants_ooxml_pdf_for_docling_ocr`` for OOXML routing.
            When OCR runs on PDF/images, RapidOCR (ONNX) is always used.

    Returns:
        Markdown string with ``--- [ Page N ] ---`` markers at page transitions.
    """
    path = Path(source_path)
    if not path.is_file():
        raise FileNotFoundError(f"Docling source not found: {path}")

    lo_cleanup: List[Path] = []
    if wants_ooxml_pdf_for_docling_ocr(
        ocr=ocr, docling_ocr_engine=docling_ocr_engine
    ) and path.suffix.lower() in _OOXML_OCR_SUFFIXES:
        try:
            pdf_path, tmp_root = _ooxml_to_pdf_via_libreoffice(path)
            path = pdf_path
            lo_cleanup.append(tmp_root)
        except Exception as e:
            logger.warning(
                "OOXML→PDF for OCR failed (%s); using native Docling Word pipeline",
                e,
            )

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
            TableFormerMode,
        )
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )
    except ImportError as e:
        raise ImportError(_DOC_NOT_INSTALLED_MSG) from e

    try:
        suffix = path.suffix.lower()
        uses_std_pdf = _suffix_uses_standard_pdf_pipeline(suffix)
        effective_ocr = wants_ooxml_pdf_for_docling_ocr(
            ocr=ocr, docling_ocr_engine=docling_ocr_engine
        )
        if uses_std_pdf:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = effective_ocr
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
            if effective_ocr:
                pipeline_options.ocr_options = RapidOcrOptions()
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
                }
            )
        else:
            converter = DocumentConverter()

        result = converter.convert(str(path))
        if not result or not result.document:
            raise RuntimeError("Docling conversion produced no document")

        return _docling_items_to_markdown(result.document)
    finally:
        for d in lo_cleanup:
            shutil.rmtree(d, ignore_errors=True)
