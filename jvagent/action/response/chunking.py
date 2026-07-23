"""Utilities for simulating streaming by chunking whole content.

Primary function for ResponseBus simulated streaming is chunk_text_by_lm_tokens():
it uses the same tokenizer as common LLMs (tiktoken cl100k_base) so boundaries
match real token streaming; strings with no spaces are split into subword tokens.
"""

import logging
import re
from typing import Generator

logger = logging.getLogger(__name__)

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


# Default encoding used by GPT-4 / Claude (cl100k_base); matches common LLM tokenization.
_DEFAULT_ENCODING_NAME = "cl100k_base"


def _get_encoding():
    """Return tiktoken encoding for LM tokenization, or None if unavailable."""
    if not _TIKTOKEN_AVAILABLE:
        return None
    try:
        return tiktoken.get_encoding(_DEFAULT_ENCODING_NAME)
    except Exception as e:
        logger.debug("tiktoken encoding unavailable for chunking: %s", e)
        return None


def chunk_text_by_lm_tokens(text: str) -> Generator[str, None, None]:
    """Split text using language-model tokenization for simulated streaming.

    Uses the same tokenizer as common LLMs (cl100k_base, e.g. GPT-4). Yields
    one token at a time, so strings with no spaces are split into subword
    tokens rather than emitted as a single chunk.

    No chunk-size settings; boundaries are determined by the tokenizer.

    Args:
        text: Full text content to chunk

    Yields:
        One token (decoded string) per chunk
    """
    if not text:
        return
    encoding = _get_encoding()
    if encoding is not None:
        try:
            token_ids = encoding.encode(text)
            for tid in token_ids:
                chunk = encoding.decode([tid])
                if chunk:
                    yield chunk
            return
        except Exception as e:
            logger.debug("tiktoken encode/decode failed, using fallback: %s", e)
    # Fallback when tiktoken unavailable: yield by character so no-space strings still stream.
    for c in text:
        if c:
            yield c
