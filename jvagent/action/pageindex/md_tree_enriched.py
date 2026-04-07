"""Enriched Markdown → tree pipeline (ISO-style merge, hierarchy, content_type, enabled).

Lives outside ``core/``; imports LLM helpers from vendored ``core.utils``.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

from jvagent.action.pageindex.core.utils import (
    count_tokens,
    create_clean_structure_for_description,
    format_structure,
    generate_doc_description,
    generate_node_summary,
    structure_to_list,
    write_node_id,
)


async def get_node_summary(node, summary_token_threshold=200, model=None):
    node_text = node.get("text")
    num_tokens = count_tokens(node_text, model=model)
    if num_tokens < summary_token_threshold:
        return node_text
    return await generate_node_summary(node, model=model)


async def generate_summaries_for_structure_md(
    structure, summary_token_threshold, model=None
):
    nodes = structure_to_list(structure)
    tasks = [
        get_node_summary(
            node, summary_token_threshold=summary_token_threshold, model=model
        )
        for node in nodes
    ]
    summaries = await asyncio.gather(*tasks)

    for node, summary in zip(nodes, summaries):
        if not node.get("nodes"):
            node["summary"] = summary
        else:
            node["prefix_summary"] = summary
    return structure


def extract_nodes_from_markdown(markdown_content):
    header_pattern = r"^(#{1,6})\s+(.+)$"
    code_block_pattern = r"^```"
    node_list = []

    lines = markdown_content.split("\n")
    in_code_block = False

    for line_num, line in enumerate(lines, 1):
        stripped_line = line.strip()

        if re.match(code_block_pattern, stripped_line):
            in_code_block = not in_code_block
            continue

        if not stripped_line:
            continue

        if not in_code_block:
            match = re.match(header_pattern, stripped_line)
            if match:
                title = match.group(2).strip()
                node_list.append({"node_title": title, "line_num": line_num})

    return node_list, lines


def extract_node_text_content(node_list, markdown_lines):
    all_nodes = []
    for node in node_list:
        line_content = markdown_lines[node["line_num"] - 1]
        header_match = re.match(r"^(#{1,6})", line_content)

        if header_match is None:
            print(
                f"Warning: Line {node['line_num']} does not contain a valid header: "
                f"'{line_content}'"
            )
            continue

        processed_node = {
            "title": node["node_title"],
            "line_num": node["line_num"],
            "level": len(header_match.group(1)),
        }
        all_nodes.append(processed_node)

    for i, node in enumerate(all_nodes):
        start_line = node["line_num"] - 1
        if i + 1 < len(all_nodes):
            end_line = all_nodes[i + 1]["line_num"] - 1
        else:
            end_line = len(markdown_lines)

        node["text"] = "\n".join(markdown_lines[start_line:end_line]).strip()
    return all_nodes


_CLAUSE_ONLY_TITLE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)*"
    r"|[A-Z]\.\d+(?:\.\d+)*"
    r"|(?:Annex|Appendix)\s+[A-Z0-9]+"
    r")\s*$",
    re.IGNORECASE,
)


def merge_adjacent_clause_headings(node_list, markdown_lines, max_line_gap=40):
    if len(node_list) < 2:
        return node_list

    def is_clause_title(title):
        t = (title or "").strip().rstrip(".")
        return bool(_CLAUSE_ONLY_TITLE.match(t))

    merged = []
    i = 0
    while i < len(node_list):
        a = node_list[i]
        if i + 1 < len(node_list):
            b = node_list[i + 1]
            gap = b["line_num"] - a["line_num"]
            if (
                a["level"] == b["level"]
                and gap <= max_line_gap
                and is_clause_title(a["title"])
                and not is_clause_title(b["title"])
            ):
                start_line = a["line_num"] - 1
                next_idx = i + 2
                if next_idx < len(node_list):
                    end_line = node_list[next_idx]["line_num"] - 1
                else:
                    end_line = len(markdown_lines)
                merged.append(
                    {
                        "title": f"{a['title'].strip()} {b['title'].strip()}",
                        "line_num": a["line_num"],
                        "level": a["level"],
                        "text": "\n".join(markdown_lines[start_line:end_line]).strip(),
                    }
                )
                i += 2
                continue
        merged.append(dict(a))
        i += 1

    if len(merged) < len(node_list):
        print(
            f"Merged {len(node_list) - len(merged)} adjacent clause/title heading pair(s)"
        )
    return merged


def _is_merge_absorb_title(title: Optional[str]) -> bool:
    """True if this heading is boilerplate that PDF→MD often repeats at page breaks."""
    tag = _structural_tag_from_title(title)
    return tag in ("running_header", "standard_title")


def merge_running_header_blocks(node_list, markdown_lines):
    """Absorb consecutive same-level running_header / standard_title nodes into the prior section.

    PDF-to-markdown often inserts a repeated ISO/doc title as ``##`` at page breaks; without this,
    the real section (e.g. a definition) is split and Notes continue under the spurious heading.

    Does not merge when the current node is itself absorb-only (file starts with header noise).
    Chains multiple consecutive absorb titles at the same level as the parent section.
    """
    if len(node_list) < 2:
        return node_list

    merged: List[Dict[str, Any]] = []
    i = 0
    n = len(node_list)
    while i < n:
        cur = node_list[i]
        j = i + 1
        while (
            j < n
            and _is_merge_absorb_title(node_list[j]["title"])
            and node_list[j]["level"] == cur["level"]
        ):
            j += 1
        if j - i - 1 > 0 and not _is_merge_absorb_title(cur["title"]):
            start_line = cur["line_num"] - 1
            if j < n:
                end_line = node_list[j]["line_num"] - 1
            else:
                end_line = len(markdown_lines)
            merged.append(
                {
                    "title": cur["title"],
                    "line_num": cur["line_num"],
                    "level": cur["level"],
                    "text": "\n".join(markdown_lines[start_line:end_line]).strip(),
                }
            )
            i = j
            continue
        merged.append(dict(cur))
        i += 1

    if len(merged) < len(node_list):
        print(
            f"Merged {len(node_list) - len(merged)} running-header / standard-title split(s)"
        )
    return merged


_MD_HEADING_LINE = re.compile(r"^#{1,6}\s")


def _infer_content_shape(title: Optional[str], text: Optional[str]) -> str:
    raw = (text or "").strip()
    if not raw:
        return "empty"
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return "empty"
    body_lines = [ln for ln in lines if not _MD_HEADING_LINE.match(ln)]

    if not body_lines:
        return "heading_like"

    body_chars = sum(len(ln) for ln in body_lines)
    # Short stubs only (use body_lines, not lines, so one real paragraph under a header
    # is not misclassified when the heading line is included in ``text``).
    if len(body_lines) <= 2 and body_chars < 40:
        return "heading_like"

    return "substantive"


_FAQ_ENABLED_CONTENT_TYPES = frozenset(
    {
        "substantive",
        "appendix",
        "introduction",
    }
)


def _structural_tag_from_title(title: Optional[str]) -> Optional[str]:
    if not title or not str(title).strip():
        return None
    t = title.strip()
    tl = t.lower()

    if tl == "contents" or re.match(r"^table of contents\b", tl):
        return "table_of_contents"
    if re.match(r"^bibliography\b", tl):
        return "bibliography"
    if re.match(r"^foreword\b", tl):
        return "foreword"
    if re.match(r"^introduction\b", tl):
        return "introduction"
    if re.match(r"^©", t) or re.match(r"^copyright\b", tl) or "copyright protected" in tl:
        return "copyright"
    if re.match(r"^annex\s+", tl):
        return "appendix"
    if re.match(r"^appendix\b", tl):
        return "appendix"
    if "draft international standard" in tl:
        return "standard_title"
    if re.search(r"\biso/dis\s+\d", tl) or re.search(r"\biso\s+\d{4,5}\s+iso/dis", tl):
        return "running_header"

    return None


def _structural_tag_from_body(text: Optional[str]) -> Optional[str]:
    raw = (text or "").strip()
    if not raw or len(raw) < 200:
        return None
    pipe_rows = raw.count("\n|")
    if pipe_rows >= 8 and (raw.count(".....") > 5 or raw.count("…") > 3):
        return "table_of_contents"
    return None


def infer_content_type(title: Optional[str], text: Optional[str]) -> str:
    tag = _structural_tag_from_title(title)
    if tag is None:
        tag = _structural_tag_from_body(text)
    if tag is not None:
        return tag

    return _infer_content_shape(title, text)


def _needs_hierarchy_fill(node: Dict[str, Any]) -> bool:
    h = node.get("hierarchy")
    return h is None or (isinstance(h, list) and len(h) == 0)


def _annotate_node(node: Dict[str, Any]) -> None:
    ct = infer_content_type(node.get("title"), node.get("text"))
    node["content_type"] = ct
    node["enabled"] = ct in _FAQ_ENABLED_CONTENT_TYPES
    for child in node.get("nodes") or []:
        if isinstance(child, dict):
            _annotate_node(child)


def annotate_content_type_and_enabled(structure: Any) -> None:
    """Apply content_type and enabled to every dict node (list root, dict root, or nested)."""
    if structure is None:
        return
    if isinstance(structure, dict):
        _annotate_node(structure)
    elif isinstance(structure, list):
        for node in structure:
            if isinstance(node, dict):
                _annotate_node(node)


def _assign_hierarchy_dfs(node: Dict[str, Any], ancestors: List[str]) -> None:
    title = str(node.get("title") or "")
    chain = ancestors + [title]
    if _needs_hierarchy_fill(node):
        node["hierarchy"] = list(chain)

    parent_path = node.get("hierarchy")
    if not isinstance(parent_path, list):
        parent_path = list(chain)

    for child in node.get("nodes") or []:
        if isinstance(child, dict):
            _assign_hierarchy_dfs(child, parent_path)


def assign_hierarchy_breadcrumbs(structure: Any) -> None:
    """Set hierarchy (title breadcrumbs) when missing; never overwrites non-empty lists (Markdown)."""
    if structure is None:
        return
    if isinstance(structure, dict):
        _assign_hierarchy_dfs(structure, [])
    elif isinstance(structure, list):
        for item in structure:
            if isinstance(item, dict):
                _assign_hierarchy_dfs(item, [])


def extract_leading_clause_id(title: str) -> Optional[str]:
    if not title or not str(title).strip():
        return None
    t = title.strip()
    m = re.match(r"^(Annex\s+)([A-Z])\b", t, re.IGNORECASE)
    if m:
        return f"Annex {m.group(2).upper()}"
    m = re.match(r"^(\d+(?:\.\d+)*)", t)
    if m:
        return m.group(1)
    return None


def _immediate_parent_clause_id(clause_id: str) -> Optional[str]:
    if not clause_id or clause_id.startswith("Annex "):
        return None
    parts = clause_id.split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


def assign_inferred_hierarchy(flat_nodes):
    clause_stack = []
    md_stack = []

    for node in flat_nodes:
        title = node["title"]
        level = node["level"]
        cid = extract_leading_clause_id(title)

        while md_stack and md_stack[-1][0] >= level:
            md_stack.pop()
        md_ancestors = [t for _, t in md_stack]

        if cid and cid.startswith("Annex "):
            clause_stack = [(title, cid)]
            node["hierarchy"] = md_ancestors + [title]
            md_stack.append((level, title))
            continue

        if cid:
            parent = _immediate_parent_clause_id(cid)
            while clause_stack and clause_stack[-1][1] != parent:
                clause_stack.pop()
            node["hierarchy"] = [t for t, _ in clause_stack] + [title]
            clause_stack.append((title, cid))
            md_stack.append((level, title))
            continue

        node["hierarchy"] = md_ancestors + [title]
        md_stack.append((level, title))


def update_node_list_with_text_token_count(node_list, model=None):

    def find_all_children(parent_index, parent_level, node_list):
        children_indices = []

        for i in range(parent_index + 1, len(node_list)):
            current_level = node_list[i]["level"]

            if current_level <= parent_level:
                break

            children_indices.append(i)

        return children_indices

    result_list = node_list.copy()

    for i in range(len(result_list) - 1, -1, -1):
        current_node = result_list[i]
        current_level = current_node["level"]

        children_indices = find_all_children(i, current_level, result_list)

        node_text = current_node.get("text", "")
        total_text = node_text

        for child_index in children_indices:
            child_text = result_list[child_index].get("text", "")
            if child_text:
                total_text += "\n" + child_text

        result_list[i]["text_token_count"] = count_tokens(total_text, model=model)

    return result_list


def tree_thinning_for_index(node_list, min_node_token=None, model=None):
    def find_all_children(parent_index, parent_level, node_list):
        children_indices = []

        for i in range(parent_index + 1, len(node_list)):
            current_level = node_list[i]["level"]

            if current_level <= parent_level:
                break

            children_indices.append(i)

        return children_indices

    result_list = node_list.copy()
    nodes_to_remove = set()

    for i in range(len(result_list) - 1, -1, -1):
        if i in nodes_to_remove:
            continue

        current_node = result_list[i]
        current_level = current_node["level"]

        total_tokens = current_node.get("text_token_count", 0)

        if total_tokens < min_node_token:
            children_indices = find_all_children(i, current_level, result_list)

            children_texts = []
            for child_index in sorted(children_indices):
                if child_index not in nodes_to_remove:
                    child_text = result_list[child_index].get("text", "")
                    if child_text.strip():
                        children_texts.append(child_text)
                    nodes_to_remove.add(child_index)

            if children_texts:
                parent_text = current_node.get("text", "")
                merged_text = parent_text
                for child_text in children_texts:
                    if merged_text and not merged_text.endswith("\n"):
                        merged_text += "\n\n"
                    merged_text += child_text

                result_list[i]["text"] = merged_text

                result_list[i]["text_token_count"] = count_tokens(merged_text, model=model)

    for index in sorted(nodes_to_remove, reverse=True):
        result_list.pop(index)

    return result_list


def build_tree_from_nodes(node_list):
    if not node_list:
        return []

    stack = []
    root_nodes = []
    node_counter = 1

    for node in node_list:
        current_level = node["level"]

        tree_node = {
            "title": node["title"],
            "node_id": str(node_counter).zfill(4),
            "text": node["text"],
            "line_num": node["line_num"],
            "hierarchy": node.get("hierarchy", [node["title"]]),
            "nodes": [],
        }
        node_counter += 1

        while stack and stack[-1][1] >= current_level:
            stack.pop()

        if not stack:
            root_nodes.append(tree_node)
        else:
            parent_node, _parent_level = stack[-1]
            parent_node["nodes"].append(tree_node)

        stack.append((tree_node, current_level))

    return root_nodes


_FORMAT_ORDER_WITH_TEXT = [
    "title",
    "node_id",
    "line_num",
    "hierarchy",
    "content_type",
    "enabled",
    "summary",
    "prefix_summary",
    "text",
    "nodes",
]
_FORMAT_ORDER_NO_TEXT = [
    "title",
    "node_id",
    "line_num",
    "hierarchy",
    "content_type",
    "enabled",
    "summary",
    "prefix_summary",
    "nodes",
]


async def md_to_tree(
    md_path,
    if_thinning=False,
    min_token_threshold=None,
    if_add_node_summary="no",
    summary_token_threshold=None,
    model=None,
    if_add_doc_description="no",
    if_add_node_text="no",
    if_add_node_id="yes",
):
    with open(md_path, "r", encoding="utf-8") as f:
        markdown_content = f.read()
    line_count = markdown_content.count("\n") + 1

    print("Extracting nodes from markdown...")
    node_list, markdown_lines = extract_nodes_from_markdown(markdown_content)

    print("Extracting text content from nodes...")
    nodes_with_content = extract_node_text_content(node_list, markdown_lines)
    print("Merging clause-number + title headings where applicable...")
    nodes_with_content = merge_adjacent_clause_headings(
        nodes_with_content, markdown_lines
    )
    print("Merging page-break running-header splits where applicable...")
    nodes_with_content = merge_running_header_blocks(
        nodes_with_content, markdown_lines
    )
    print("Building hierarchy (clause + heading level)...")
    assign_inferred_hierarchy(nodes_with_content)

    if if_thinning:
        nodes_with_content = update_node_list_with_text_token_count(
            nodes_with_content, model=model
        )
        print("Thinning nodes...")
        nodes_with_content = tree_thinning_for_index(
            nodes_with_content, min_token_threshold, model=model
        )

    print("Building tree from nodes...")
    tree_structure = build_tree_from_nodes(nodes_with_content)

    if if_add_node_id == "yes":
        write_node_id(tree_structure)

    print("Formatting tree structure...")

    if if_add_node_summary == "yes":
        tree_structure = format_structure(tree_structure, order=_FORMAT_ORDER_WITH_TEXT)

        print("Generating summaries for each node...")
        tree_structure = await generate_summaries_for_structure_md(
            tree_structure,
            summary_token_threshold=summary_token_threshold,
            model=model,
        )

        if if_add_node_text == "no":
            tree_structure = format_structure(
                tree_structure, order=_FORMAT_ORDER_NO_TEXT
            )

        if if_add_doc_description == "yes":
            print("Generating document description...")
            clean_structure = create_clean_structure_for_description(tree_structure)
            doc_description = generate_doc_description(clean_structure, model=model)
            return {
                "doc_name": os.path.splitext(os.path.basename(md_path))[0],
                "doc_description": doc_description,
                "line_count": line_count,
                "structure": tree_structure,
            }
    else:
        if if_add_node_text == "yes":
            tree_structure = format_structure(
                tree_structure, order=_FORMAT_ORDER_WITH_TEXT
            )
        else:
            tree_structure = format_structure(
                tree_structure, order=_FORMAT_ORDER_NO_TEXT
            )

    return {
        "doc_name": os.path.splitext(os.path.basename(md_path))[0],
        "line_count": line_count,
        "structure": tree_structure,
    }
