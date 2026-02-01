"""Utilities for simulating streaming by chunking whole content.

Primary function for ResponseBus simulated streaming is chunk_text_by_lm_tokens():
it uses the same tokenizer as common LLMs (tiktoken cl100k_base) so boundaries
match real token streaming; strings with no spaces are split into subword tokens.
chunk_text_by_words() and chunk_text_by_chars() remain for other use cases.
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


def chunk_text_by_words(
    text: str,
    words_per_chunk: int = 5,
    min_chunk_chars: int = 1,
) -> Generator[str, None, None]:
    """Split text into chunks based on word count for simulated streaming.
    
    This creates a smooth typing effect by breaking content into natural
    word boundaries, mimicking how streaming LLMs deliver tokens.
    
    Args:
        text: Full text content to chunk
        words_per_chunk: Target number of words per chunk (default 5)
        min_chunk_chars: Minimum characters per chunk (default 1)
        
    Yields:
        Text chunks with word boundaries preserved
        
    Examples:
        >>> text = "Hello world, this is a test message"
        >>> list(chunk_text_by_words(text, words_per_chunk=3))
        ['Hello world, this', ' is a test', ' message']
    """
    if not text or not text.strip():
        return
    
    # Split on whitespace while preserving the whitespace
    # This regex splits on spaces but includes the space with the following word
    words = re.split(r'(?<=\s)(?=\S)', text)
    
    current_chunk = []
    current_length = 0
    
    for word in words:
        current_chunk.append(word)
        current_length += len(word)
        
        # Yield chunk if we've reached target word count or accumulated enough chars
        if len(current_chunk) >= words_per_chunk or current_length >= min_chunk_chars * words_per_chunk:
            chunk_text = ''.join(current_chunk)
            if chunk_text:  # Only yield non-empty chunks
                yield chunk_text
            current_chunk = []
            current_length = 0
    
    # Yield any remaining words
    if current_chunk:
        chunk_text = ''.join(current_chunk)
        if chunk_text:
            yield chunk_text


def chunk_text_by_chars(
    text: str,
    chars_per_chunk: int = 20,
) -> Generator[str, None, None]:
    """Split text into fixed-size character chunks for simulated streaming.
    
    This is simpler but may break words. Use chunk_text_by_words for better UX.
    
    Args:
        text: Full text content to chunk
        chars_per_chunk: Number of characters per chunk (default 20)
        
    Yields:
        Fixed-size text chunks
        
    Examples:
        >>> text = "Hello world"
        >>> list(chunk_text_by_chars(text, chars_per_chunk=5))
        ['Hello', ' worl', 'd']
    """
    if not text:
        return
    
    for i in range(0, len(text), chars_per_chunk):
        chunk = text[i:i + chars_per_chunk]
        if chunk:
            yield chunk
