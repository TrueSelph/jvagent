"""Text tokenization for PageIndex lexical index.

Provides consistent tokenization for both indexing and query-time operations.
No external NLP dependencies -- simple whitespace/punctuation split with
stop-word removal, optimised for section-level document content.
"""

import re
from typing import Dict, List, Tuple

_SPLIT_RE = re.compile(r"[^a-z0-9]+")

_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "own",
        "say",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "too",
        "up",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

_MIN_TOKEN_LEN = 2

_TITLE_BOOST = 3


def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric terms with stop-word removal."""
    if not text:
        return []
    tokens = _SPLIT_RE.split(text.lower())
    return [t for t in tokens if len(t) >= _MIN_TOKEN_LEN and t not in _STOP_WORDS]


def term_frequencies(tokens: List[str]) -> Dict[str, int]:
    """Count term frequencies from a token list."""
    tf: Dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


def tokenize_fields(
    title: str = "",
    text: str = "",
    summary: str = "",
    prefix_summary: str = "",
) -> Tuple[Dict[str, int], int]:
    """Tokenize document node fields and return merged TF map and total length.

    Title tokens receive a synthetic boost (repeated ``_TITLE_BOOST`` times)
    so that title matches rank higher without a separate field index.
    """
    title_tokens = tokenize(title)
    text_tokens = tokenize(text)
    summary_tokens = tokenize(summary)
    prefix_tokens = tokenize(prefix_summary)

    all_tokens = (
        title_tokens * _TITLE_BOOST + text_tokens + summary_tokens + prefix_tokens
    )
    return term_frequencies(all_tokens), len(all_tokens)
