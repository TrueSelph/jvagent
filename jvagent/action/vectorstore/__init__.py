"""VectorStore action for vector database integration.

This module provides a standard interface for vector database operations including
storage, semantic search, and retrieval. It supports multiple vector database
backends through pluggable implementations.
"""

from jvagent.action.vectorstore.base import VectorStore

# Conditionally export TypesenseVectorStore if available
try:
    from jvagent.action.vectorstore.typesense import TypesenseVectorStore

    __all__ = ["VectorStore", "TypesenseVectorStore"]
except ImportError:
    __all__ = ["VectorStore"]
