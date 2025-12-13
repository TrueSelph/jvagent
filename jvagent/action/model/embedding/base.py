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

    async def embed(self, text: str) -> List[float]:
        """Generate embedding vector for text.

        Public API for embedding generation with metrics tracking.
        Observability metrics are automatically emitted via context-based tracking.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats

        Raises:
            ValueError: If text is empty
            RuntimeError: If embedding generation fails
        """
        import time

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

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

        # Track usage (approximate token count for embeddings)
        # Most embedding APIs don't provide token counts, so we estimate
        estimated_tokens = len(text.split())
        self.track_usage({"total_tokens": estimated_tokens}, duration)

        logger.debug(
            f"Generated embedding: {len(vector)} dimensions, "
            f"{duration:.3f}s, {estimated_tokens} tokens (estimated)"
        )

        return vector

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
        pass
