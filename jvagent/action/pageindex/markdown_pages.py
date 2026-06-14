"""Page markers in Markdown (e.g. Docling ``--- [ Page N ] ---``) and structure annotation."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# Matches lines like: --- [ Page 12 ] ---
_PAGE_MARKER_RE = re.compile(
    r"^\s*---\s*\[\s*Page\s+(\d+)\s*\]\s*---\s*$",
    re.IGNORECASE,
)


def strip_page_markers_and_build_line_page_map(raw: str) -> Tuple[str, Dict[int, int]]:
    """Remove page-marker lines and build 1-based line index → page number for remaining text.

    If the raw content contains no page markers, returns ``(raw, {})`` so callers can
    skip page annotation (plain Markdown).
    """
    lines = raw.split("\n")
    if not any(_PAGE_MARKER_RE.match(line) for line in lines):
        return raw, {}

    current_page = 1
    cleaned_lines: List[str] = []
    line_to_page: Dict[int, int] = {}

    for line in lines:
        m = _PAGE_MARKER_RE.match(line)
        if m:
            current_page = int(m.group(1))
            continue
        cleaned_lines.append(line)
        line_to_page[len(cleaned_lines)] = current_page

    return "\n".join(cleaned_lines), line_to_page


def _flatten_tree_nodes_preorder(structure: Any) -> List[Dict[str, Any]]:
    """Preorder list of section nodes (dicts with line_num, nodes)."""
    out: List[Dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "line_num" in obj and "title" in obj:
                out.append(obj)
            for child in obj.get("nodes") or []:
                walk(child)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(structure)
    return out


def annotate_markdown_structure_pages(
    structure: Any,
    line_to_page: Dict[int, int],
    num_lines: int,
    *,
    source_line_offset: int = 0,
) -> None:
    """Set physical_index, start_index, end_index on structure dicts in-place.

    Args:
        structure: Nested PageIndex markdown tree (dict or list of dicts).
        line_to_page: Maps source line number (1-based) to physical page (from markers).
        num_lines: Total line count of the **parsed** markdown (after any synthetic heading).
        source_line_offset: If md_to_tree prepended lines (e.g. ``# Document``), node
            ``line_num`` values are offset; map back into ``line_to_page`` (disk/cleaned lines).
    """
    if not line_to_page or not structure:
        return

    flat = _flatten_tree_nodes_preorder(structure)
    if not flat:
        return

    off = max(0, int(source_line_offset))

    for i, node in enumerate(flat):
        start_ln = int(node.get("line_num") or 0)
        if start_ln < 1:
            continue
        if i + 1 < len(flat):
            end_ln = int(flat[i + 1]["line_num"]) - 1
        else:
            end_ln = num_lines
        if end_ln < start_ln:
            end_ln = start_ln

        disk_start = max(1, start_ln - off)
        disk_end = max(1, end_ln - off)

        pages = [
            line_to_page[ln]
            for ln in range(disk_start, disk_end + 1)
            if ln in line_to_page
        ]
        if not pages:
            continue
        phys = line_to_page.get(disk_start, pages[0])
        end_phys = max(pages)
        node["physical_index"] = phys
        node["start_index"] = phys
        node["end_index"] = end_phys
