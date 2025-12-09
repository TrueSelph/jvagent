"""Base VectorStore action interface for vector database operations.

This module provides the abstract base class for all VectorStore implementations.
VectorStore actions enable semantic search capabilities for parameters, flows,
canned responses, and glossary terms.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


class VectorStore(Action, ABC):
    """Base action for vector database operations.

    VectorStore provides a standard interface for storing documents with embeddings
    and performing semantic similarity search. This enables semantic search capabilities
    for PersonaAction components like parameters, flows, canned responses, and glossary terms.

    Subclasses should implement the abstract methods to provide specific vector database
    backend implementations (e.g., Typesense, Pinecone, Weaviate).

    Attributes:
        embedder_type: Type of embedder to use for generating embeddings (deprecated, use embedding_model_action_type)
        embedding_model_action_type: Entity type of EmbeddingModelAction to use (e.g., "OpenAIEmbeddingModelAction")
        default_collection: Default collection name to use if not specified
    """

    # Configuration
    embedder_type: str = attribute(
        default="sentence-transformers",
        description="Type of embedder to use (deprecated, use embedding_model_action_type)",
    )
    embedding_model_action_type: str = attribute(
        default="",
        description="Entity type of EmbeddingModelAction to use (e.g., 'OpenAIEmbeddingModelAction'). If empty, uses first available.",
    )
    default_collection: str = attribute(
        default="default",
        description="Default collection name to use if not specified",
    )

    @abstractmethod
    async def store(
        self,
        collection: str,
        documents: List[Dict[str, Any]],
        embeddings: Optional[List[List[float]]] = None,
    ) -> List[str]:
        """Store documents with optional embeddings in a collection.

        Args:
            collection: Collection name to store documents in
            documents: List of documents to store. Each document should be a dict
                with at least 'id' and 'content' fields. Additional fields are stored as metadata.
            embeddings: Optional pre-computed embeddings. If None, embeddings will be
                generated automatically using the embedder.

        Returns:
            List of document IDs that were stored

        Raises:
            ValueError: If documents are invalid or collection doesn't exist
            RuntimeError: If storage operation fails
        """
        pass

    @abstractmethod
    async def search(
        self,
        collection: str,
        query: str,
        k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar documents using semantic similarity.

        Args:
            collection: Collection name to search in
            query: Query string to search for
            k: Number of similar documents to return (default: 10)
            filters: Optional metadata filters to apply (e.g., {"tag": "important"})

        Returns:
            List of similar documents, each containing:
            - document: The document data
            - score: Similarity score (higher is more similar)
            - distance: Distance metric (lower is more similar)
            - metadata: Document metadata

        Raises:
            ValueError: If collection doesn't exist or query is invalid
            RuntimeError: If search operation fails
        """
        pass

    @abstractmethod
    async def delete_document(
        self,
        collection: str,
        document_ids: List[str],
    ) -> bool:
        """Delete documents from a collection (VectorStore-specific).

        Args:
            collection: Collection name to delete from
            document_ids: List of document IDs to delete

        Returns:
            True if all deletions succeeded, False otherwise
        """
        pass

    async def create_collection(
        self,
        collection: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create a new collection (optional operation).

        Some vector databases require explicit collection creation, while others
        create collections automatically on first use. This method provides a
        standard interface for collection creation.

        Args:
            collection: Collection name to create
            schema: Optional schema definition for the collection

        Returns:
            True if collection was created or already exists, False otherwise
        """
        # Default implementation: collections are created automatically
        # Subclasses can override if explicit creation is needed
        return True

    async def delete_collection(self, collection: str) -> bool:
        """Delete a collection and all its documents (optional operation).

        Args:
            collection: Collection name to delete

        Returns:
            True if collection was deleted, False otherwise
        """
        # Default implementation: not supported
        # Subclasses can override if deletion is supported
        return False

    async def _get_embedding_model(self) -> Optional[Any]:
        """Get the embedding model action for generating embeddings.

        Returns:
            EmbeddingModelAction instance or None if not found
        """
        from jvagent.action.model.embedding.base import EmbeddingModelAction

        agent = await self.get_agent()
        if not agent:
            logger.warning("VectorStore: Agent not found, cannot retrieve embedding model")
            return None

        if self.embedding_model_action_type:
            embedding_model = await agent.get_action_by_type(self.embedding_model_action_type)
            if embedding_model and isinstance(embedding_model, EmbeddingModelAction):
                return embedding_model

        # Fallback: find first available EmbeddingModelAction
        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=True)
            for action in all_actions:
                if isinstance(action, EmbeddingModelAction):
                    return action

        logger.warning("VectorStore: No embedding model action found")
        return None

    async def _embed_text(self, text: str) -> List[float]:
        """Generate embedding vector for text using the configured embedding model.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats

        Raises:
            RuntimeError: If embedding model is not available or embedding fails
        """
        embedding_model = await self._get_embedding_model()
        if not embedding_model:
            raise RuntimeError(
                "Embedding model not found. Configure embedding_model_action_type or register an EmbeddingModelAction."
            )

        try:
            vector = await embedding_model.embed(text)
            return vector
        except Exception as e:
            logger.error(f"VectorStore: Failed to generate embedding: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate embedding: {e}") from e

    async def healthcheck(self) -> Dict[str, Any]:
        """Perform health check for the vector store.

        Returns:
            Dictionary with health information including:
            - healthy: Boolean indicating if the store is healthy
            - collections: List of available collections
            - embedder: Information about the embedder
        """
        embedder_info = {"type": self.embedder_type}
        if self.embedding_model_action_type:
            embedder_info["model_action_type"] = self.embedding_model_action_type

        return {
            "healthy": True,
            "collections": [],
            "embedder": embedder_info,
        }
