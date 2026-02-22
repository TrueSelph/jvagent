"""Embedding model implementations.

This package contains embedding model provider implementations for generating
vector embeddings from text.
"""

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from jvagent.action.model.embedding import endpoints  # noqa: F401
from jvagent.action.model.embedding.base import EmbeddingModelAction
from jvagent.action.model.embedding.generic import GenericEmbeddingModelAction
from jvagent.action.model.embedding.huggingface import HuggingFaceEmbeddingModelAction
from jvagent.action.model.embedding.openai import OpenAIEmbeddingModelAction
from jvagent.action.model.embedding.openrouter import OpenRouterEmbeddingModelAction

__all__ = [
    "EmbeddingModelAction",
    "OpenAIEmbeddingModelAction",
    "HuggingFaceEmbeddingModelAction",
    "OpenRouterEmbeddingModelAction",
    "GenericEmbeddingModelAction",
]
