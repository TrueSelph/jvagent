"""VectorStore endpoints for document CRUD operations.

This module provides RESTful endpoints for managing documents in vector stores,
including adding, listing, getting, updating, and deleting documents.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import EndpointField, endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.action.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/documents",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["VectorStore"],
    response=success_response(
        data={
            "document_ids": ResponseField(
                field_type=List[str],
                description="List of document IDs that were stored",
                example=["doc_1", "doc_2"],
            ),
            "collection": ResponseField(
                field_type=str,
                description="Collection name where documents were stored",
                example="agent_collection",
            ),
        }
    ),
)
async def add_documents_endpoint(
    action_id: str,
    documents: List[Dict[str, Any]] = EndpointField(
        ...,
        description="List of documents to add to the vector store. Each document must have 'content' (required) and optionally 'id' (auto-generated if not provided) and 'metadata'.",
        examples=[
            [
                {
                    "id": "doc_1",
                    "content": "This is the first document content",
                    "metadata": {
                        "source": "manual",
                        "category": "knowledge",
                        "tags": ["important", "reference"],
                    },
                },
                {
                    "content": "This document will get an auto-generated ID",
                    "metadata": {
                        "source": "api",
                        "priority": "high",
                    },
                },
            ]
        ],
    ),
    collection: Optional[str] = EndpointField(
        default=None,
        description="Collection name - defaults to agent ID if not specified",
        examples=["custom_collection", "agent_collection"],
    ),
) -> Dict[str, Any]:
    """Add documents to the vector store.


    **Request Body:**

    - **documents** (List[Dict[str, Any]]): List of documents to add. Each document must have:

        - **id** (str, optional): Document ID - will be auto-generated if not provided
        - **content** (str, required): Text content to embed and store
        - **metadata** (Dict[str, Any], optional): Additional metadata dictionary

    - **collection** (str, optional): Collection name - defaults to agent ID if not specified


    **Request Body Example:**

    ```json
    {
        "documents": [
            {
                "id": "doc_1",
                "content": "This is the first document content",
                "metadata": {
                    "source": "manual",
                    "category": "knowledge",
                    "tags": ["important", "reference"]
                }
            },
            {
                "content": "This document will get an auto-generated ID",
                "metadata": {
                    "source": "api",
                    "priority": "high"
                }
            }
        ],
        "collection": "custom_collection_name"
    }
    ```


    **Args:**

    - action_id: VectorStore action ID
    - documents: List of documents to add
    - collection: Optional collection name (defaults to agent-specific collection)


    **Returns:**

    Dictionary with document_ids and collection name
    """
    action = await VectorStore.get(action_id)
    if not action or not isinstance(action, VectorStore):
        raise ResourceNotFoundError(f"VectorStore action not found: {action_id}")

    if not documents:
        raise ValidationError("documents list cannot be empty")

    # Prepare documents for storage
    prepared_docs = []
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise ValidationError(f"Document at index {i} must be a dictionary")

        doc_id = doc.get("id")
        if not doc_id:
            # Generate ID if not provided
            import uuid

            doc_id = f"doc_{uuid.uuid4().hex[:12]}"
            doc["id"] = doc_id

        content = doc.get("content")
        if not content:
            raise ValidationError(f"Document {doc_id} must have 'content' field")

        prepared_docs.append(
            {
                "id": str(doc_id),
                "content": str(content),
                **{k: v for k, v in doc.items() if k not in ("id", "content")},
            }
        )

    # Store documents (collection resolution happens in store method via _resolve_collection_name)
    document_ids = await action.store(
        collection=collection,  # Pass None if not specified, let _resolve_collection_name handle it
        documents=prepared_docs,
    )

    # Get the resolved collection name for response
    resolved_collection = await action._resolve_collection_name(collection)

    return {
        "document_ids": document_ids,
        "collection": resolved_collection,
    }


@endpoint(
    "/actions/{action_id}/documents",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["VectorStore"],
    response=success_response(
        data={
            "items": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of documents",
                example=[
                    {
                        "id": "doc_1",
                        "content": "Sample content",
                        "metadata": {"tag": "important"},
                    }
                ],
            ),
            "pagination": ResponseField(
                field_type=Dict[str, Any],
                description="Pagination information matching ObjectPager format",
                example={
                    "total_items": 100,
                    "total_pages": 5,
                    "current_page": 1,
                    "page_size": 20,
                    "has_previous": False,
                    "has_next": True,
                    "previous_page": None,
                    "next_page": 2,
                    "start_index": 0,
                    "end_index": 19,
                },
            ),
            "collection": ResponseField(
                field_type=str,
                description="Collection name",
                example="agent_collection",
            ),
        }
    ),
)
async def list_documents_endpoint(
    action_id: str,
    collection: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """List documents in the vector store with pagination.


    **Args:**

    - action_id: VectorStore action ID
    - collection: Optional collection name (defaults to agent-specific collection)
    - page: Page number (1-based, default: 1)
    - page_size: Number of items per page (default: 20)
    - filters: Optional metadata filters (e.g., `{"tag": "important"}`)


    **Returns:**

    Dictionary with items and pagination info
    """
    action = await VectorStore.get(action_id)
    if not action or not isinstance(action, VectorStore):
        raise ResourceNotFoundError(f"VectorStore action not found: {action_id}")

    if page < 1:
        raise ValidationError("page must be >= 1")
    if page_size < 1:
        raise ValidationError("page_size must be >= 1")

    # List documents (collection resolution happens in list_documents method via _resolve_collection_name)
    result = await action.list_documents(
        collection=collection,  # Pass None if not specified, let _resolve_collection_name handle it
        page=page,
        page_size=page_size,
        filters=filters,
    )

    # Get the resolved collection name for response
    resolved_collection = await action._resolve_collection_name(collection)

    return {
        **result,
        "collection": resolved_collection,
    }


@endpoint(
    "/actions/{action_id}/documents/{document_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["VectorStore"],
    response=success_response(
        data={
            "document": ResponseField(
                field_type=Dict[str, Any],
                description="Document data",
                example={
                    "id": "doc_1",
                    "content": "Sample content",
                    "metadata": {"tag": "important"},
                },
            ),
            "collection": ResponseField(
                field_type=str,
                description="Collection name",
                example="agent_collection",
            ),
        }
    ),
)
async def get_document_endpoint(
    action_id: str,
    document_id: str,
    collection: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a single document by ID.


    **Args:**

    - action_id: VectorStore action ID
    - document_id: Document ID
    - collection: Optional collection name (defaults to agent-specific collection)


    **Returns:**

    Dictionary with document data and collection name
    """
    action = await VectorStore.get(action_id)
    if not action or not isinstance(action, VectorStore):
        raise ResourceNotFoundError(f"VectorStore action not found: {action_id}")

    # Get document (collection resolution happens in get_document method via _resolve_collection_name)
    document = await action.get_document(
        collection=collection,  # Pass None if not specified, let _resolve_collection_name handle it
        document_id=document_id,
    )

    # Get the resolved collection name for response
    resolved_collection = await action._resolve_collection_name(collection)

    if not document:
        raise ResourceNotFoundError(
            f"Document {document_id} not found in collection {resolved_collection}"
        )

    return {
        "document": document,
        "collection": resolved_collection,
    }


@endpoint(
    "/actions/{action_id}/documents/{document_id}",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["VectorStore"],
    response=success_response(
        data={
            "document_id": ResponseField(
                field_type=str,
                description="Updated document ID",
                example="doc_1",
            ),
            "collection": ResponseField(
                field_type=str,
                description="Collection name",
                example="agent_collection",
            ),
        }
    ),
)
async def update_document_endpoint(
    action_id: str,
    document_id: str,
    content: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    collection: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a document in the vector store.


    **Args:**

    - action_id: VectorStore action ID
    - document_id: Document ID to update
    - content: Optional new content (will regenerate embedding if provided)
    - metadata: Optional new metadata
    - collection: Optional collection name (defaults to agent-specific collection)


    **Returns:**

    Dictionary with document_id and collection name
    """
    action = await VectorStore.get(action_id)
    if not action or not isinstance(action, VectorStore):
        raise ResourceNotFoundError(f"VectorStore action not found: {action_id}")

    if content is None and metadata is None:
        raise ValidationError("Either content or metadata must be provided")

    # Update document (collection resolution happens in update_document method via _resolve_collection_name)
    success = await action.update_document(
        collection=collection,  # Pass None if not specified, let _resolve_collection_name handle it
        document_id=document_id,
        content=content,
        metadata=metadata,
    )

    # Get the resolved collection name for response
    resolved_collection = await action._resolve_collection_name(collection)

    if not success:
        raise ResourceNotFoundError(
            f"Document {document_id} not found in collection {resolved_collection}"
        )

    return {
        "document_id": document_id,
        "collection": resolved_collection,
    }


@endpoint(
    "/actions/{action_id}/documents/{document_id}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["VectorStore"],
    response=success_response(
        data={
            "document_id": ResponseField(
                field_type=str,
                description="Deleted document ID",
                example="doc_1",
            ),
            "collection": ResponseField(
                field_type=str,
                description="Collection name",
                example="agent_collection",
            ),
        }
    ),
)
async def delete_document_endpoint(
    action_id: str,
    document_id: str,
    collection: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a document from the vector store.


    **Args:**

    - action_id: VectorStore action ID
    - document_id: Document ID to delete
    - collection: Optional collection name (defaults to agent-specific collection)


    **Returns:**

    Dictionary with document_id and collection name
    """
    action = await VectorStore.get(action_id)
    if not action or not isinstance(action, VectorStore):
        raise ResourceNotFoundError(f"VectorStore action not found: {action_id}")

    # Delete document (collection resolution happens in delete_document method via _resolve_collection_name)
    success = await action.delete_document(
        collection=collection,  # Pass None if not specified, let _resolve_collection_name handle it
        document_ids=[document_id],
    )

    # Get the resolved collection name for response
    resolved_collection = await action._resolve_collection_name(collection)

    if not success:
        raise ResourceNotFoundError(
            f"Document {document_id} not found in collection {resolved_collection}"
        )

    return {
        "document_id": document_id,
        "collection": resolved_collection,
    }
