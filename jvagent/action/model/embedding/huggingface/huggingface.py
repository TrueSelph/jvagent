"""HuggingFace embedding model action implementation.

Provides integration with HuggingFace's Inference API for generating vector embeddings.
"""

import logging
from typing import Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


class HuggingFaceEmbeddingModelAction(EmbeddingModelAction):
    """HuggingFace Inference API implementation for embeddings.

    Implements the EmbeddingModelAction interface for HuggingFace's Inference API.
    Supports sentence-transformers and other embedding models.

    Configuration:
        api_key: HuggingFace API key (from environment or config)
        api_endpoint: API endpoint (defaults to https://api-inference.huggingface.co)
        model: Model identifier (e.g., 'sentence-transformers/all-MiniLM-L6-v2')
        embedding_dimensions: Expected dimensions (0 = auto-detect)

    Examples:
        >>> action = await HuggingFaceEmbeddingModelAction.get(action_id)
        >>> vector = await action.embed("Hello world")
    """

    api_endpoint: str = attribute(
        default="https://api-inference.huggingface.co",
        description="HuggingFace Inference API endpoint URL",
    )
    model: str = attribute(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="HuggingFace model identifier",
    )

    # HTTP client (not persisted)
    _http_client: Optional[httpx.AsyncClient] = attribute(private=True, default=None)

    # Model dimension mapping (for auto-detection)
    _model_dimensions: Dict[str, int] = attribute(
        private=True,
        default_factory=lambda: {
            "sentence-transformers/all-MiniLM-L6-v2": 384,
            "sentence-transformers/all-mpnet-base-v2": 768,
            "sentence-transformers/all-MiniLM-L12-v2": 384,
        },
    )

    async def on_register(self) -> None:
        """Initialize HTTP client and validate configuration."""
        await super().on_register()

        # Validate API key
        if not self.api_key:
            logger.warning(
                f"HuggingFace embedding action {self.label} has no API key configured"
            )

        # Initialize HTTP client
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        # Auto-detect dimensions from model if not set
        if self.embedding_dimensions == 0 and self.model in self._model_dimensions:
            self.embedding_dimensions = self._model_dimensions[self.model]

        logger.debug(
            f"HuggingFace embedding HTTP client initialized "
            f"(endpoint: {self.api_endpoint}, model: {self.model}, "
            f"dimensions: {self.embedding_dimensions})"
        )

    async def on_disable(self) -> None:
        """Close HTTP client connections."""
        await super().on_disable()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("HuggingFace embedding HTTP client closed")

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding using HuggingFace Inference API.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        if not self._http_client:
            await self.on_register()

        # Build request payload
        payload = {"inputs": text}

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/pipeline/feature-extraction/{self.model}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            # HuggingFace returns nested list: [[0.1, 0.2, ...]]
            # Extract the first (and only) embedding vector
            if isinstance(data, list) and len(data) > 0:
                vector = data[0] if isinstance(data[0], list) else data
            else:
                vector = data

            # Ensure it's a list of floats
            if not isinstance(vector, list):
                raise ValueError(f"Unexpected response format: {type(vector)}")

            # Update dimensions if auto-detection
            if self.embedding_dimensions == 0:
                self.embedding_dimensions = len(vector)

            return [float(x) for x in vector]

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HuggingFace embedding API error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"HuggingFace embedding API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"HuggingFace embedding API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"HuggingFace embedding failed: {e}", exc_info=True)
            raise

