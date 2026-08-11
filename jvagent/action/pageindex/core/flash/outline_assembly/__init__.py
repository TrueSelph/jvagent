"""Outline assembly chain. This module turns heading candidates and labeled section regions into the final
nested outline tree. It groups candidates by numbering depth, style signature,
script compatibility, document order, and local clusters, then serializes the
tree into the public PageIndex JSON shape.
"""

import math
from typing import Any, Callable, Optional

from sortedcontainers import SortedKeyList

from ..model import (
    Block,
    Line,
    Rect,
    _round_half_up_to_int,
    _trim_unicode_ws,
    alignment_code,
    avg_char_width,
    block_text,
    center_aligned,
    deaccented_text,
    dominant_font_size,
    dominant_style_of,
    first_span_of,
    heading_score,
    info_weight,
    is_caps_heavy,
    is_upper_dominant,
    last_line_of,
    last_span,
    left_aligned,
    left_edge_key,
    letter_count,
    numbering_kind,
    numbering_text,
    numbering_value,
    raw_text_of_line,
    reading_order_key,
    rect_union,
    right_aligned,
    style_key,
    x_aligned,
)

# Section-keyword trie shared with outline filtering.
from ..outline import SECTION_KEYWORD_TRIE
from ..stats import (
    ScriptHistogram,
    column_index_of,
    dominant_script_family,
)
from ..stats import style_key as style_key_fn
from ..stats import (
    tally_scripts,
)
from ..tokens import (
    Token,
    TokenView,
    TrieConfig,
)
from ..tokens import avg_char_width as avg_char_width_fn
from ..tokens import (
    build_trie,
    enumerate_tokens,
    first_anchor_span,
    first_token,
    is_char_token,
    is_word_token,
    last_token,
    set_case_fold,
    tokenize_block,
    trie_full_match,
    trie_prefix_match,
    wrap_tokens,
)
from .assembly import (
    _flatten_outline_nodes,
    _heading_appears_at_page_top,
    assemble_outline,
    build_heading_from_block,
    compute_max_heading_gap,
    has_table_or_prominent,
    is_landscape_or_empty,
    mark_outline_block_types,
    outline_to_dict_tree,
)
from .candidates import (
    HeadingCandidate,
    OutlineNode,
    _compare_block_order,
    _viewport_y_fraction,
    cached_signature,
    compare_heading_order,
    has_style_neighbor,
    heading_order_key,
    heading_signature,
    is_in_oo_range,
    is_script_compatible,
    parent_signature,
)
from .cliques import (
    CliqueFilterContext,
    CliqueTreeBuilder,
    CliqueTreeNode,
    append_tree_child,
    block_style_signature,
    can_share_heading_style,
    compare_block_order,
    descend_to_deepest_last,
    detect_body_headings,
    find_ancestor_next_sibling,
    find_keyword_clique,
    heading_precedes_line,
    interleave_clusters,
    is_member_of_tree,
    partition_candidates,
)
from .selection import (
    HierarchyStack,
    extract_sub_headings,
    extract_top_level_headings,
    find_parent_heading,
    is_appendix_nesting_ok,
    is_chapter_outline_valid,
    is_outline_valid,
    min_font_distance,
    push_heading_to_state,
    should_reject_heading,
)
from .style_context import (
    NumberingTrie,
    OutlineContext,
    OutlineState,
    StyleCluster,
    _apply_heading_to_state,
    compare_heading_depth,
    count_sibling_numberings,
    has_conflict_in_context,
    insert_numbering,
    is_compatible_with_context,
    pick_style_bucket,
)

# --------------------------------------------------------------------------- #
# Numbering-pattern clique selection.
# --------------------------------------------------------------------------- #




__all__ = [
    "HeadingCandidate",
    "OutlineNode",
    "compare_heading_order",
    "heading_order_key",
    "compare_heading_depth",
    "is_script_compatible",
    "heading_signature",
    "parent_signature",
    "cached_signature",
    "is_in_oo_range",
    "has_style_neighbor",
    "pick_style_bucket",
    "has_conflict_in_context",
    "is_compatible_with_context",
    "StyleCluster",
    "OutlineContext",
    "NumberingTrie",
    "insert_numbering",
    "count_sibling_numberings",
    "OutlineState",
    "find_keyword_clique",
    "detect_body_headings",
    "CliqueFilterContext",
    "partition_candidates",
    "interleave_clusters",
    "push_heading_to_state",
    "should_reject_heading",
    "find_parent_heading",
    "HierarchyStack",
    "extract_sub_headings",
    "min_font_distance",
    "extract_top_level_headings",
    "is_outline_valid",
    "is_chapter_outline_valid",
    "mark_outline_block_types",
    "compute_max_heading_gap",
    "has_table_or_prominent",
    "build_heading_from_block",
    "assemble_outline",
    "outline_to_dict_tree",
]
