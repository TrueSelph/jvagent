"""Base class for embedding model actions.

This module provides the abstract base class for all embedding model implementations.
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.model.base import BaseModelAction

logger = logging.getLogger(__name__)


class EmbeddingModelAction(BaseModelAction, ABC):
    """Base class for embedding model actions.

    This abstract class defines the standard interface that all embedding model
    providers must implement. It provides a unified interface for generating
    vector embeddings from text.

    Attributes:
        embedding_dimensions: Expected embedding dimensions (0 = auto-detect)

    Examples:
        Programmatic usage:
        >>> embedding_model = await OpenAIEmbeddingModelAction.get(action_id)
        >>> vector = await embedding_model.embed("Hello world")
        >>> print(f"Embedding dimensions: {len(vector)}")
    """

    embedding_dimensions: int = attribute(
        default=0, description="Expected embedding dimensions (0 = auto-detect)", ge=0
    )

    def _consume_recorded_usage_tokens(self) -> int:
        tokens = int(getattr(self, "_recorded_usage_tokens", 0) or 0)
        self._recorded_usage_tokens = 0
        return tokens

    def _record_usage_tokens(self, tokens: int) -> None:
        self._recorded_usage_tokens = max(0, int(tokens or 0))

    async def embed(
        self, text: str, calling_action_name: Optional[str] = None
    ) -> List[float]:
        """Generate embedding vector for text.

        Public API for embedding generation with metrics tracking.
        Observability metrics are automatically emitted via context-based tracking.

        Args:
            text: Text to embed
            calling_action_name: Optional name of the action calling this method

        Returns:
            Embedding vector as list of floats

        Raises:
            ValueError: If text is empty
            RuntimeError: If embedding generation fails
        """
        import time

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        # Store calling_action_name for observability
        self._calling_action_name = calling_action_name

        # Start timing
        start_time = time.time()

        # Generate embedding
        try:
            vector = await self._embed(text)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate embedding: {e}") from e

        # Calculate duration
        duration = time.time() - start_time

        # Update dimensions if auto-detection
        if self.embedding_dimensions == 0 and vector:
            self.embedding_dimensions = len(vector)

        # Track usage. Prefer provider-reported token counts when available.
        total_tokens = self._consume_recorded_usage_tokens()
        if total_tokens <= 0:
            total_tokens = len(text.split())
        await self.track_usage({"total_tokens": total_tokens}, duration)

        logger.debug(
            f"Generated embedding: {len(vector)} dimensions, "
            f"{duration:.3f}s, {total_tokens} tokens"
        )

        return vector

    async def embed_batch(
        self, texts: List[str], calling_action_name: Optional[str] = None
    ) -> List[List[float]]:
        """Generate embedding vectors for multiple texts in one call."""
        import time

        if not texts:
            raise ValueError("Texts list cannot be empty")
        if any(not text or not text.strip() for text in texts):
            raise ValueError("All texts must be non-empty")

        self._calling_action_name = calling_action_name
        start_time = time.time()

        try:
            vectors = await self._embed_batch(texts)
        except Exception as e:
            logger.error(f"Batch embedding generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate batch embeddings: {e}") from e

        duration = time.time() - start_time
        if self.embedding_dimensions == 0 and vectors and vectors[0]:
            self.embedding_dimensions = len(vectors[0])

        total_tokens = self._consume_recorded_usage_tokens()
        if total_tokens <= 0:
            total_tokens = sum(len(text.split()) for text in texts)
        await self.track_usage({"total_tokens": total_tokens}, duration)
        return vectors

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Default batch implementation (provider subclasses can override)."""
        vectors: List[List[float]] = []
        for text in texts:
            vectors.append(await self._embed(text))
        return vectors

    @abstractmethod
    async def _embed(self, text: str) -> List[float]:
        """Generate embedding vector for text (provider implementation).

        This method must be implemented by provider subclasses to handle
        the actual API call and return the embedding vector.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
