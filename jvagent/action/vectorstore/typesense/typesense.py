"""Typesense vector database integration for VectorStore.

This module provides integration with Typesense for vector storage and semantic search.
Typesense is a fast, typo-tolerant search engine that supports vector search.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)

# Try to import Typesense client
try:
    import typesense

    TYPESENSE_AVAILABLE = True
except ImportError:
    TYPESENSE_AVAILABLE = False
    logger.warning(
        "Typesense client not available. "
        "Install typesense package to use TypesenseVectorStore: pip install typesense"
    )


class TypesenseVectorStore(VectorStore):
    """VectorStore implementation using Typesense.

    This implementation uses Typesense for vector storage and semantic search.
    Typesense supports vector search with embeddings and provides fast, typo-tolerant search.

    Attributes:
        host: Typesense server host
        port: Typesense server port
        protocol: Protocol to use (http or https)
        api_key: Typesense API key
        connection_timeout_seconds: Connection timeout in seconds
        embedding_dimensions: Number of dimensions for embeddings (default: 384 for sentence-transformers)
    """

    # Configuration
    host: str = attribute(
        default="localhost",
        description="Typesense server host",
    )
    port: int = attribute(
        default=8108,
        description="Typesense server port",
    )
    protocol: str = attribute(
        default="http",
        description="Protocol to use (http or https)",
    )
    query_by_field: str = attribute(
        default="content",
        description="Field to use for query_by in Typesense text/hybrid searches; not used for pure vector search",
    )
    vector_field: str = attribute(
        default="vector",
        description="Name of the vector field in the collection schema (default: 'vector')",
    )
    api_key: str = attribute(
        default="",
        description="Typesense API key",
    )
    connection_timeout_seconds: int = attribute(
        default=2,
        description="Connection timeout in seconds",
    )
    embedding_dimensions: int = attribute(
        default=384,
        description="Number of dimensions for embeddings (default: 384 for sentence-transformers)",
    )

    # Internal state
    _client: Optional[Any] = None
    _collections: Dict[str, bool] = {}  # Track created collections

    async def _initialize_client(self) -> None:
        """Initialize Typesense client connection.
        
        This method can be called multiple times safely - it will only initialize
        the client if it doesn't already exist. Called automatically during
        on_register() and when client is needed for operations.
        """
        if self._client is not None:
            return

        if not TYPESENSE_AVAILABLE:
            raise RuntimeError(
                "Typesense client not available. "
                "Please install the typesense package: pip install typesense"
            )

        if not self.api_key:
            raise ValueError("Typesense API key is required")

        try:
            self._client = typesense.Client(
                {
                    "nodes": [
                        {
                            "host": self.host,
                            "port": str(self.port),
                            "protocol": self.protocol,
                        }
                    ],
                    "api_key": self.api_key,
                    "connection_timeout_seconds": self.connection_timeout_seconds,
                }
            )
            logger.debug(f"Typesense client initialized: {self.protocol}://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to initialize Typesense client: {e}")
            raise RuntimeError(f"Could not initialize Typesense client: {e}")

    async def on_register(self) -> None:
        """Called when action is registered during installation.
        
        Validates configuration. Client initialization is handled automatically
        by the base class via _initialize_client(). This method should only be
        called once during action registration.
        """
        await super().on_register()
        
        logger.info(f"TypesenseVectorStore registered: {self.protocol}://{self.host}:{self.port}")

    async def _cleanup_client(self) -> None:
        """Clean up Typesense client connection.
        
        Typesense client doesn't have an explicit close method,
        so we just clear the reference.
        """
        if self._client:
            self._client = None
            logger.debug("Typesense client cleared")

    async def _get_or_create_collection(self, collection: str) -> None:
        """Get or create a Typesense collection.

        Args:
            collection: Collection name
        """
        if collection in self._collections:
            return

        # Check if collection exists
        try:
            self._client.collections[collection].retrieve()
            self._collections[collection] = True
            return
        except Exception:
            # Collection doesn't exist, create it
            pass

        # Create collection schema
        # Note: Typesense doesn't allow 'id' as default_sorting_field
        # String fields need to be explicitly marked as sortable
        # enable_nested_fields is required for object type fields
        schema = {
            "name": collection,
            "fields": [
                {"name": "id", "type": "string"},
                {"name": "content", "type": "string", "sort": True},
                {
                    "name": "vector",
                    "type": "float[]",
                    "num_dim": self.embedding_dimensions,
                },
                {"name": "metadata", "type": "object", "optional": True},
            ],
            "default_sorting_field": "content",
            "enable_nested_fields": True,
        }

        try:
            self._client.collections.create(schema)
            self._collections[collection] = True
            logger.debug(f"Created Typesense collection: {collection}")
        except Exception as e:
            logger.error(f"Failed to create collection {collection}: {e}")
            raise

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using the configured embedding model.

        Args:
            text: Text to embed

        Returns:
            Embedding vector

        Raises:
            RuntimeError: If embedding model is not available
        """
        # Use base class method to generate embedding via EmbeddingModelAction
        return await self._embed_text(text)

    async def store(
        self,
        collection: str,
        documents: List[Dict[str, Any]],
        embeddings: Optional[List[List[float]]] = None,
    ) -> List[str]:
        """Store documents with optional embeddings in a collection.

        Args:
            collection: Collection name to store documents in
            documents: List of documents to store. Each document should have:
                - 'id': Document ID (string)
                - 'content': Text content to embed
                - Additional fields stored as metadata
            embeddings: Optional pre-computed embeddings. If None, embeddings will be
                generated (requires embedding model implementation).

        Returns:
            List of document IDs that were stored
        """
        # Ensure client is initialized
        await self._initialize_client()
        
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        # Resolve and ensure collection exists
        collection = await self._resolve_collection_name(collection)
        await self._get_or_create_collection(collection)

        stored_ids = []
        for i, doc in enumerate(documents):
            doc_id = doc.get("id")
            if not doc_id:
                logger.warning(f"Document missing 'id' field, skipping: {doc}")
                continue

            content = doc.get("content", "")
            metadata = {k: v for k, v in doc.items() if k not in ("id", "content")}

            # Get or generate embedding
            if embeddings and i < len(embeddings):
                vector = embeddings[i]
            else:
                vector = await self._generate_embedding(content)

            # Prepare document for Typesense
            typesense_doc = {
                "id": str(doc_id),
                "content": content,
                "vector": vector,
            }
            if metadata:
                typesense_doc["metadata"] = metadata

            try:
                self._client.collections[collection].documents.upsert(typesense_doc)
                stored_ids.append(str(doc_id))
            except Exception as e:
                logger.error(f"Failed to store document {doc_id}: {e}")

        return stored_ids

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
            k: Number of similar documents to return
            filters: Optional metadata filters (converted to Typesense filter_by format)

        Returns:
            List of similar documents, each containing:
            - document: The document data
            - score: Similarity score (higher is more similar)
            - distance: Distance metric (lower is more similar)
            - metadata: Document metadata
        """
        # Ensure client is initialized
        await self._initialize_client()
        
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        # Resolve collection and generate query embedding
        collection = await self._resolve_collection_name(collection)
        query_vector = await self._generate_embedding(query)

        # Build filter_by string from filters
        filter_by = None
        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, list):
                    filter_parts.append(f"{key}:={','.join(map(str, value))}")
                else:
                    filter_parts.append(f"{key}:={value}")
            if filter_parts:
                filter_by = " && ".join(filter_parts)

        # Build search parameters using multi_search endpoint for vector queries
        # This avoids the 4000 character limit on query strings
        # For pure vector search, we don't use query_by (that's for text/hybrid search)
        # Typesense vector query format: vector_field_name:([0.1,0.2,0.3], k:10)
        search_parameters: Dict[str, Any] = {
            "collection": collection,
            "q": "*",
            "vector_query": f'{self.vector_field}:([{",".join(map(str, query_vector))}], k:{k})',
            "per_page": k,
        }

        if filter_by:
            search_parameters["filter_by"] = filter_by

        try:
            # Use multi_search endpoint for vector queries to avoid query string length limits
            # The multi_search endpoint sends the request in the body as JSON, avoiding URL query string limits
            # This is necessary because vector queries with embeddings can be very long
            multi_search_request = {
                "searches": [search_parameters]
            }
            
            # Perform multi_search - this sends the request in the body, not the URL
            # The vector_query string is sent as part of the JSON body, not in the URL
            results_response = self._client.multi_search.perform(multi_search_request, {})
            
            # Extract results from multi_search response
            # multi_search returns {"results": [{"hits": [...], ...}]}
            if results_response and "results" in results_response and len(results_response["results"]) > 0:
                results = results_response["results"][0]
            else:
                logger.warning("Typesense multi_search returned no results")
                results = {"hits": []}

            # Convert to standard format
            formatted_results = []
            for hit in results.get("hits", []):
                doc = hit.get("document", {})
                # Typesense vector search returns similarity scores
                # The score is typically a distance metric (lower is better)
                # We'll use the rank as a proxy for similarity if no explicit score
                rank = hit.get("rank", len(formatted_results) + 1)
                # Convert rank to similarity score (higher rank = lower similarity)
                # Use inverse rank as similarity score
                max_results = len(results.get("hits", []))
                similarity_score = 1.0 - (rank - 1) / max_results if max_results > 0 else 0.0
                
                # If there's a vector distance in the hit, use that
                if "vector_distance" in hit:
                    distance = hit["vector_distance"]
                    similarity_score = 1.0 / (1.0 + distance)  # Convert distance to similarity

                formatted_results.append(
                    {
                        "document": {
                            "id": doc.get("id", ""),
                            "content": doc.get("content", ""),
                            **doc.get("metadata", {}),
                        },
                        "score": similarity_score,
                        "distance": 1.0 - similarity_score,
                        "metadata": doc.get("metadata", {}),
                    }
                )

            return formatted_results
        except Exception as e:
            logger.error(f"Error during Typesense search: {e}")
            raise RuntimeError(f"Typesense search failed: {e}")

    async def delete_document(
        self,
        collection: str,
        document_ids: List[str],
    ) -> bool:
        """Delete documents from a collection."""
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        collection = await self._resolve_collection_name(collection)

        all_succeeded = True
        for doc_id in document_ids:
            try:
                self._client.collections[collection].documents[str(doc_id)].delete()
            except Exception as e:
                logger.error(f"Failed to delete document {doc_id}: {e}")
                all_succeeded = False

        return all_succeeded

    async def create_collection(
        self,
        collection: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create a new collection.

        Args:
            collection: Collection name to create
            schema: Optional schema definition (not used, uses default schema)

        Returns:
            True if collection was created or already exists
        """
        try:
            collection = await self._resolve_collection_name(collection)
            await self._get_or_create_collection(collection)
            return True
        except Exception as e:
            logger.error(f"Failed to create collection {collection}: {e}")
            return False

    async def delete_collection(self, collection: str) -> bool:
        """Delete a collection and all its documents.

        Args:
            collection: Collection name to delete

        Returns:
            True if collection was deleted
        """
        # Ensure client is initialized
        await self._initialize_client()
        
        if not TYPESENSE_AVAILABLE or not self._client:
            return False

        try:
            collection = await self._resolve_collection_name(collection)
            self._client.collections[collection].delete()
            if collection in self._collections:
                del self._collections[collection]
            return True
        except Exception as e:
            logger.error(f"Failed to delete collection {collection}: {e}")
            return False

    async def healthcheck(self) -> Dict[str, Any]:
        """Perform health check for the vector store.

        Returns:
            Dictionary with health information
        """
        base_health = await super().healthcheck()
        base_health.update(
            {
                "typesense_available": TYPESENSE_AVAILABLE,
                "collections": list(self._collections.keys()),
                "client_initialized": self._client is not None,
                "host": self.host,
                "port": self.port,
                "protocol": self.protocol,
            }
        )

        # Check Typesense health
        if self._client:
            try:
                health = self._client.health.retrieve()
                base_health["typesense_health"] = health.get("ok", False)
            except Exception as e:
                logger.warning(f"Failed to check Typesense health: {e}")
                base_health["typesense_health"] = False

        return base_health

    async def get_document(
        self,
        collection: str,
        document_id: str,
        include_vector: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Get a single document by ID.

        Args:
            collection: Collection name
            document_id: Document ID
            include_vector: Whether to include the vector in the response (default: False)

        Returns:
            Document data if found, None otherwise
        """
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        collection = await self._resolve_collection_name(collection)

        try:
            doc = self._client.collections[collection].documents[str(document_id)].retrieve()
            result = {
                "id": doc.get("id", document_id),
                "content": doc.get("content", ""),
                "metadata": doc.get("metadata", {}),
            }
            if include_vector:
                result["vector"] = doc.get("vector", [])
            return result
        except Exception as e:
            logger.debug(f"Document {document_id} not found in collection {collection}: {e}")
            return None

    async def list_documents(
        self,
        collection: str,
        page: int = 1,
        page_size: int = 20,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """List documents in a collection with pagination.

        Args:
            collection: Collection name
            page: Page number (1-based)
            page_size: Number of items per page
            filters: Optional metadata filters

        Returns:
            Dictionary with items and pagination info matching ObjectPager format
        """
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        collection = await self._resolve_collection_name(collection)

        # Build filter_by string from filters
        filter_by = None
        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, list):
                    filter_parts.append(f"metadata.{key}:={','.join(map(str, value))}")
                else:
                    filter_parts.append(f"metadata.{key}:={value}")
            if filter_parts:
                filter_by = " && ".join(filter_parts)

        try:
            # Typesense pagination: page is 1-based, per_page is the page size
            search_params = {
                "q": "*",
                "per_page": page_size,
                "page": page,
            }

            if filter_by:
                search_params["filter_by"] = filter_by

            # Use search endpoint to list all documents
            results = self._client.collections[collection].documents.search(search_params)

            # Extract documents and pagination info
            hits = results.get("hits", [])
            found = results.get("found", 0)
            page_num = results.get("page", page)
            per_page = results.get("per_page", page_size)

            items = []
            for hit in hits:
                doc = hit.get("document", {})
                items.append({
                    "id": doc.get("id", ""),
                    "content": doc.get("content", ""),
                    "metadata": doc.get("metadata", {}),
                })

            total_pages = (found + per_page - 1) // per_page if found > 0 else 0
            start_index = (page_num - 1) * per_page
            end_index = min(start_index + len(items) - 1, found - 1) if found > 0 else None

            return {
                "items": items,
                "pagination": {
                    "total_items": found,
                    "total_pages": total_pages,
                    "current_page": page_num,
                    "page_size": per_page,
                    "has_previous": page_num > 1,
                    "has_next": page_num < total_pages if total_pages > 0 else False,
                    "previous_page": page_num - 1 if page_num > 1 else None,
                    "next_page": page_num + 1 if page_num < total_pages else None,
                    "start_index": start_index,
                    "end_index": end_index,
                },
            }
        except Exception as e:
            logger.error(f"Error listing documents from collection {collection}: {e}")
            raise RuntimeError(f"Failed to list documents: {e}") from e

    async def update_document(
        self,
        collection: str,
        document_id: str,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update a document in the collection.

        Args:
            collection: Collection name
            document_id: Document ID to update
            content: Optional new content (will regenerate embedding if provided)
            metadata: Optional new metadata

        Returns:
            True if update succeeded, False otherwise
        """
        if not TYPESENSE_AVAILABLE or not self._client:
            raise RuntimeError("Typesense client not available")

        collection = await self._resolve_collection_name(collection)

        try:
            # Get existing document with vector
            existing_doc = await self.get_document(collection, document_id, include_vector=True)
            if not existing_doc:
                return False

            # Prepare update document
            update_doc = {
                "id": str(document_id),
            }

            # Update content if provided (regenerate embedding)
            if content is not None:
                update_doc["content"] = content
                vector = await self._generate_embedding(content)
                update_doc["vector"] = vector
            else:
                # Keep existing content and vector
                update_doc["content"] = existing_doc.get("content", "")
                update_doc["vector"] = existing_doc.get("vector", [])

            # Update metadata
            if metadata is not None:
                update_doc["metadata"] = metadata
            else:
                update_doc["metadata"] = existing_doc.get("metadata", {})

            # Upsert the updated document
            self._client.collections[collection].documents[str(document_id)].update(update_doc)
            return True
        except Exception as e:
            logger.error(f"Error updating document {document_id} in collection {collection}: {e}")
            return False

