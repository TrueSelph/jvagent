"""OpenAI embedding model action implementation.

Provides integration with OpenAI's Embeddings API for generating vector embeddings.
"""

import logging
from typing import Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


class OpenAIEmbeddingModelAction(EmbeddingModelAction):
    """OpenAI embeddings API implementation.

    Implements the EmbeddingModelAction interface for OpenAI's Embeddings API.
    Supports all OpenAI embedding models including text-embedding-3-small,
    text-embedding-3-large, and text-embedding-ada-002.

    Configuration:
        api_key: OpenAI API key (from environment or config)
        api_endpoint: API endpoint (defaults to https://api.openai.com/v1)
        model: Model identifier (e.g., 'text-embedding-3-small', 'text-embedding-ada-002')
        embedding_dimensions: Expected dimensions (0 = auto-detect from model)

    Examples:
        >>> action = await OpenAIEmbeddingModelAction.get(action_id)
        >>> vector = await action.embed("Hello world")
    """

    api_endpoint: str = attribute(
        default="https://api.openai.com/v1", description="OpenAI API endpoint URL"
    )
    model: str = attribute(
        default="text-embedding-3-small", description="OpenAI embedding model identifier"
    )
    provider: str = attribute(
        default="openai", description="Provider name"
    )

    # Model dimension mapping (for auto-detection)
    _model_dimensions: Dict[str, int] = attribute(
        private=True,
        default_factory=lambda: {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        },
    )

    async def on_register(self) -> None:
        """Called when action is registered during installation.
        
        Validates configuration. HTTP client initialization is handled
        by the base class. This method should only be called once during
        action registration.
        """
        await super().on_register()

        # Validate API key
        if not self.api_key:
            logger.warning(f"OpenAI embedding action {self.label} has no API key configured")

        # Auto-detect dimensions from model if not set
        if self.embedding_dimensions == 0 and self.model in self._model_dimensions:
            self.embedding_dimensions = self._model_dimensions[self.model]

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding using OpenAI API.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        await self._initialize_http_client()

        # Build request payload
        payload = {
            "model": self.model,
            "input": text,
        }

        # Add dimensions parameter if specified and model supports it
        if self.embedding_dimensions > 0 and "text-embedding-3" in self.model:
            payload["dimensions"] = self.embedding_dimensions

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/embeddings",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            # Extract embedding vector
            embedding_data = data["data"][0]
            vector = embedding_data["embedding"]

            # Update dimensions if auto-detection
            if self.embedding_dimensions == 0:
                self.embedding_dimensions = len(vector)

            return vector

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI embedding API error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.TimeoutException as e:
            logger.error(f"OpenAI embedding API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"OpenAI embedding API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}", exc_info=True)
            raise

