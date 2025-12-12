"""Generic embedding model action implementation.

Provides a generic RESTful API implementation for custom embedding services.
Supports configurable request/response formats for integrating with various APIs.
"""

import logging
from typing import Any, List, Optional

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


class GenericEmbeddingModelAction(EmbeddingModelAction):
    """Generic RESTful API implementation for embeddings.

    Supports custom embedding APIs with configurable request/response formats.
    Useful for integrating with custom embedding services or OpenAI-compatible APIs.

    Configuration:
        api_key: API key for authentication
        api_endpoint: Base API endpoint URL
        model: Model identifier
        embedding_dimensions: Expected dimensions (0 = auto-detect)
        api_format: API format - "openai", "huggingface", or "generic"
        request_path: API path (defaults based on format)
        request_key: Key for text in request payload (defaults based on format)
        response_path: JSON path to embedding in response (defaults based on format)

    Examples:
        >>> action = await GenericEmbeddingModelAction.get(action_id)
        >>> action.api_format = "openai"
        >>> vector = await action.embed("Hello world")
    """

    api_format: str = attribute(
        default="openai",
        description="API format: 'openai', 'huggingface', or 'generic'",
    )
    request_path: str = attribute(
        default="", description="API path (defaults based on format if empty)"
    )
    request_key: str = attribute(
        default="", description="Key for text in request payload (defaults based on format if empty)"
    )
    response_path: str = attribute(
        default="",
        description="JSON path to embedding in response (defaults based on format if empty)",
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
                f"Generic embedding action {self.label} has no API key configured"
            )

    def _get_request_path(self) -> str:
        """Get API request path based on format."""
        if self.request_path:
            return self.request_path

        if self.api_format == "openai":
            return "/embeddings"
        elif self.api_format == "huggingface":
            return f"/pipeline/feature-extraction/{self.model}"
        else:
            return "/embed"  # Generic default

    def _get_request_key(self) -> str:
        """Get request key for text payload based on format."""
        if self.request_key:
            return self.request_key

        if self.api_format == "openai":
            return "input"
        elif self.api_format == "huggingface":
            return "inputs"
        else:
            return "text"  # Generic default

    def _extract_embedding(self, data: Any) -> List[float]:
        """Extract embedding vector from response based on format."""
        if self.response_path:
            # Custom JSON path (e.g., "data.0.embedding")
            parts = self.response_path.split(".")
            result = data
            for part in parts:
                if part.isdigit():
                    result = result[int(part)]
                else:
                    result = result[part]
            return [float(x) for x in result]

        # Format-based extraction
        if self.api_format == "openai":
            # OpenAI format: {"data": [{"embedding": [...]}]}
            return [float(x) for x in data["data"][0]["embedding"]]
        elif self.api_format == "huggingface":
            # HuggingFace format: [[0.1, 0.2, ...]]
            vector = data[0] if isinstance(data, list) and len(data) > 0 else data
            return [float(x) for x in vector]
        else:
            # Generic: assume response is the vector or {"embedding": [...]}
            if isinstance(data, list):
                return [float(x) for x in data]
            elif isinstance(data, dict) and "embedding" in data:
                return [float(x) for x in data["embedding"]]
            else:
                raise ValueError(f"Unexpected response format: {type(data)}")

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding using generic RESTful API.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        await self._initialize_http_client()

        # Build request payload
        request_key = self._get_request_key()
        payload = {request_key: text}

        # Add model if specified
        if self.model:
            payload["model"] = self.model

        # Build request path
        path = self._get_request_path()
        url = f"{self.api_endpoint.rstrip('/')}{path}"

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            # Extract embedding vector
            vector = self._extract_embedding(data)

            # Update dimensions if auto-detection
            if self.embedding_dimensions == 0:
                self.embedding_dimensions = len(vector)

            return vector

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Generic embedding API error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Generic embedding API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Generic embedding API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Generic embedding failed: {e}", exc_info=True)
            raise

