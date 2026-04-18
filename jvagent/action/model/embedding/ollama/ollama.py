"""Ollama embedding model action implementation.

Provides integration with Ollama's native embedding API for generating vectors.
"""

import logging
from typing import List

import httpx
from jvspatial.core.annotations import attribute

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


class OllamaEmbeddingModelAction(EmbeddingModelAction):
    """Ollama native embeddings API implementation."""

    api_endpoint: str = attribute(
        default="http://localhost:11434", description="Ollama API endpoint URL"
    )
    model: str = attribute(
        default="nomic-embed-text", description="Ollama embedding model identifier"
    )
    provider: str = attribute(default="ollama", description="Provider name")

    async def on_register(self) -> None:
        """Called when action is registered during installation."""
        await super().on_register()
        logger.info(
            f"Ollama embedding action registered: {self.label} (model: {self.model})"
        )

    async def _embed(self, text: str) -> List[float]:
        """Generate embedding using Ollama native embeddings endpoint."""
        await self._initialize_http_client()
        payload = {"model": self.model, "input": text}

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/api/embed",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            self._record_usage_tokens(int(data.get("prompt_eval_count", 0) or 0))

            # /api/embed supports batches, but this action accepts a single input string.
            embeddings = data.get("embeddings", [])
            if not embeddings:
                raise ValueError("No embeddings returned from Ollama API")

            vector = embeddings[0]
            if self.embedding_dimensions == 0:
                self.embedding_dimensions = len(vector)
            return vector

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Ollama embedding API error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Ollama embedding API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Ollama embedding API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Ollama embedding failed: {e}", exc_info=True)
            raise

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings in one Ollama batch request."""
        await self._initialize_http_client()
        payload = {"model": self.model, "input": texts}

        try:
            response = await self._http_client.post(  # type: ignore[union-attr]
                f"{self.api_endpoint}/api/embed",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            self._record_usage_tokens(int(data.get("prompt_eval_count", 0) or 0))
            embeddings = data.get("embeddings", [])
            if not embeddings:
                raise ValueError("No embeddings returned from Ollama API")
            if len(embeddings) != len(texts):
                raise ValueError("Ollama embedding batch response count mismatch")

            if self.embedding_dimensions == 0 and embeddings[0]:
                self.embedding_dimensions = len(embeddings[0])
            return embeddings
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Ollama embedding batch API error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Ollama embedding batch API timeout: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Ollama embedding batch API request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Ollama embedding batch failed: {e}", exc_info=True)
            raise
