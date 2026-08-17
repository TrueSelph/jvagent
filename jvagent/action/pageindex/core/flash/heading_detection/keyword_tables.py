"""Dictionary-backed keyword tries, keyword sets, and numbering tables."""

from __future__ import annotations

import json
import re
from pathlib import Path

import regex as regex_module  # Unicode \p{...} property classes.

from ..model import (
    _UNICODE_WHITESPACE_CLASS,
    Block,
    CharStats,
    Line,
    _strip_diacritics,
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
    is_punct_category,
    is_upper_dominant,
    is_word_category,
    last_line_of,
    last_span,
    left_aligned,
    letter_count,
    magnitude_ratio,
    numbering_kind,
    numbering_text,
    numbering_value,
    punct_count,
    raw_text_of_line,
    right_aligned,
    same_x_extent,
    same_y_extent,
    style_key,
    to_number,
    x_aligned,
    x_centers_close,
    y_overlaps,
)
from ..tokens import (
    COMMA_CHARS,
    Token,
    TokenView,
    TrieConfig,
    build_trie,
    enumerate_tokens,
    first_anchor_span,
    first_token,
    is_char_token,
    is_trimmable_token,
    is_word_token,
    last_token,
    last_token_anchor,
    set_case_fold,
    strip_leading_if_in,
    strip_trailing_comma,
    strip_trie_match,
    token_numeric_value,
    tokenize_block,
    trie_full_match,
    trie_prefix_match,
    trim_trailing_punct,
    wrap_tokens,
)

# --------------------------------------------------------------------------- #
# Dictionary tries (case-folded) #
# --------------------------------------------------------------------------- #


_DICT_PATH = Path(__file__).parent.parent / "data" / "dictionaries.json"
_DICTS = json.loads(_DICT_PATH.read_text(encoding="utf-8"))

SECTION_KEYWORDS_TRIE = build_trie(
    _DICTS.get("section_keywords", []), set_case_fold(TrieConfig(), True)
)  # general sections
ABSTRACT_KEYWORDS_TRIE = build_trie(
    _DICTS.get("abstract_keywords", []), set_case_fold(TrieConfig(), True)
)  # abstract
REFERENCES_TRIE = build_trie(
    _DICTS.get("references", []), set_case_fold(TrieConfig(), True)
)  # references
APPENDIX_SECTION_TRIE = build_trie(
    _DICTS.get("appendices_dict", []), set_case_fold(TrieConfig(), True)
)  # appendix
INTRODUCTION_SECTION_TRIE = build_trie(
    _DICTS.get("introduction_dict", []), set_case_fold(TrieConfig(), True)
)  # introduction
BOX_KEYWORD_TRIE = build_trie(["box"], set_case_fold(TrieConfig(), True))
KEYWORDS_SECTION_TRIE = build_trie(
    _DICTS.get("keywords_dict", []), set_case_fold(TrieConfig(), True)
)  # keywords
CHAPTER_WORDS_TRIE = build_trie(
    _DICTS.get("chapter_words", []), set_case_fold(TrieConfig(), True)
)  # chapter
APPENDIX_KEYWORDS_TRIE = build_trie(
    _DICTS.get("appendix_keywords", []), set_case_fold(TrieConfig(), True)
)  # appendix (hi)


# Whole-text lookup sets use normalized lowercase strings. The normalization is
# NFD -> strip combining marks (U+0300-U+036F) -> NFC; it is diacritic stripping,
# not compatibility folding.
def _normalize_text_key(text: str) -> str:
    return _strip_diacritics(text)


# Whole-text lookup sets for abstract and references headings.
# Abstract headings are matched diacritic-insensitively; references are not.
ABSTRACT_KEYWORDS_SET = frozenset(
    _strip_diacritics(text_value.lower())
    for text_value in _DICTS.get("abstract_keywords", [])
)
REFERENCES_SET = frozenset(
    text_value.lower() for text_value in _DICTS.get("references", [])
)


# Numbered heading prefix: leading ASCII/fullwidth 1-9, followed by Unicode
# numeric code points, punctuation, and whitespace or uppercase lookahead. The
# leading class deliberately excludes fullwidth zero (U+FF10).
NUMBERED_PREFIX_RE = regex_module.compile(
    r"^([1-9１-９]\p{Number}*)[ .-](?:[" + _UNICODE_WHITESPACE_CLASS + r"]|\p{Lu})"
)
# Equation separator fallback. This intentionally matches only the literal
# string pattern around ``p{Number}``, so the branch remains inert for ordinary
# numeric text.
DEAD_DIGIT_RE = re.compile(r"^.p\{Number\}+.$")

# Trie of equation-like keywords ("equation", "eqn", "eq", plus multilingual
# variants).
EQUATION_KEYWORDS_TRIE = build_trie(
    [
        "equation",
        "equation.",
        "eqn",
        "eqn.",
        "eq",
        "eq.",
        "ecuación",
        "equação",
        "gleichung",
        "equazione",
        "ekvation",
        "yhtälö",
        "ligning",
        "persamaan",
        "denklem",
        "ecuația",
        "equació",
        "rovnica",
        "rovnice",
        "równanie",
        "vergelijking",
        "jednadžba",
        "jöfnu",
        "võrrand",
        "vienādojums",
        "lygtis",
        "enačba",
        "egyenlet",
        "phương trình",
        "εξίσωση",
        "方程",
        "방정식",
        "уравнение",
        "рівняння",
        "раўнанне",
        "једначина",
    ],
    set_case_fold(TrieConfig(), True),
)


# Roman and English number words used by heading numbering detectors.
ENGLISH_WORD_TO_NUMBER = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
ROMAN_NUMERAL_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
}


# Special-character weights used by equation-content scoring.
FORMULA_CHAR_WEIGHTS = {
    "=": 10,
    "{": 5,
    "}": 5,
    "+": 5,
    "/": 3,
    "*": 3,
    "-": 1,
    "~": 1,
    "[": 1,
    "]": 1,
    "(": 1,
    ")": 1,
}
