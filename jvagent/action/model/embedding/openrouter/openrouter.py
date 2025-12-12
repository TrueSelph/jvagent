"""OpenRouter embedding model action implementation.

Provides integration with OpenRouter's API for generating vector embeddings.
OpenRouter's API is OpenAI-compatible and supports OpenAI embedding models.
"""

import logging
from typing import List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


class OpenRouterEmbeddingModelAction(EmbeddingModelAction):
    """OpenRouter embeddings API implementation.

    Implements the EmbeddingModelAction interface using OpenRouter's API, which is
    OpenAI-compatible. Supports OpenAI embedding models through OpenRouter.

    Configuration:
        api_key: OpenRouter API key (from environment or config)
        api_endpoint: OpenRouter API endpoint (defaults to https://openrouter.ai/api/v1)
        model: Model identifier (e.g., 'openai/text-embedding-3-small')
        embedding_dimensions: Expected dimensions (0 = auto-detect)
        http_referer: HTTP Referer header (optional, for OpenRouter)
        site_name: Site name for OpenRouter (optional)

    Examples:
        >>> action = await OpenRouterEmbeddingModelAction.get(action_id)
        >>> vector = await action.embed("Hello world")
    """

    api_endpoint: str = attribute(
        default="https://openrouter.ai/api/v1", description="OpenRouter API endpoint URL"
    )
    model: str = attribute(
        default="openai/text-embedding-3-small",
        description="OpenRouter model identifier (provider/model format)",
    )
    http_referer: str = attribute(
        default="", description="HTTP Referer header for OpenRouter (optional)"
    )
    site_name: str = attribute(
        default="jvagent", description="Site name for OpenRouter (optional)"
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
            logger.warning(
                f"OpenRouter embedding action {self.label} has no API key configured"
            )

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding using OpenRouter API (OpenAI-compatible).

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        await self._initialize_http_client()

        # Build request payload (OpenAI-compatible format)
        payload = {
            "model": self.model,
            "input": text,
        }

        # Add dimensions parameter if specified
        if self.embedding_dimensions > 0 and "text-embedding-3" in self.model:
            payload["dimensions"] = self.embedding_dimensions

        # Build headers
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Add OpenRouter-specific headers
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.site_name:
            headers["X-Title"] = self.site_name

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/embeddings",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            # Extract embedding vector (OpenAI-compatible format)
            embedding_data = data["data"][0]
            vector = embedding_data["embedding"]

            # Update dimensions if auto-detection
            if self.embedding_dimensions == 0:
                self.embedding_dimensions = len(vector)

            return vector

        except httpx.HTTPStatusError as e:
            logger.error(
                f"OpenRouter embedding API error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"OpenRouter embedding API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"OpenRouter embedding API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"OpenRouter embedding failed: {e}", exc_info=True)
            raise

