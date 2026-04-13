"""API endpoints for embedding model actions.

Provides HTTP endpoints that wrap the programmatic embedding model action interface
for generating vector embeddings from text.
"""

import logging
from typing import Any, Dict, List

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.model.embedding.base import EmbeddingModelAction

logger = logging.getLogger(__name__)


# ============================================================================
# Embed Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/embed",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Embedding Model Action"],
    response=success_response(
        data={
            "embedding": ResponseField(
                field_type=List[float],
                description="Vector embedding as list of floats",
                example=[0.1, -0.2, 0.3, 0.4, -0.5],
            ),
            "dimensions": ResponseField(
                field_type=int,
                description="Number of dimensions in the embedding vector",
                example=1536,
            ),
            "metrics": ResponseField(
                field_type=Dict[str, Any],
                description="Query metrics including token usage and duration",
                example={
                    "total_tokens": 20,
                    "duration": 0.234,
                },
            ),
            "model": ResponseField(
                field_type=str,
                description="Model identifier used",
                example="text-embedding-3-small",
            ),
            "provider": ResponseField(
                field_type=str,
                description="Provider name",
                example="openai",
            ),
        }
    ),
)
async def embed_text(
    action_id: str,
    text: str,
) -> Dict[str, Any]:
    """Generate a vector embedding for text.

    This endpoint generates a vector embedding (dense numerical representation)
    for the provided text using the specified embedding model action.


    Embeddings are useful for:

    - Semantic search and similarity matching
    - Clustering and classification
    - Retrieval-augmented generation (RAG)
    - Recommendation systems


    **Args:**

    - action_id: ID of the embedding model action to use
    - text: Text to embed (cannot be empty)


    **Returns:**

    Dictionary containing:

    - **embedding**: List of floats representing the vector embedding
    - **dimensions**: Number of dimensions in the embedding vector
    - **metrics**: Query metrics including token usage and duration
    - **model**: Model identifier used
    - **provider**: Provider name (openai, huggingface, openrouter, etc.)


    **Raises:**

    - ResourceNotFoundError: If action not found
    - ValueError: If text is empty


    **Examples:**

    Basic embedding:

    ```json
    POST /actions/abc123/embed
    {
        "text": "The quick brown fox jumps over the lazy dog"
    }
    ```

    Response:

    ```json
    {
        "embedding": [0.1, -0.2, 0.3, ...],
        "dimensions": 1536,
        "metrics": {
            "total_tokens": 9,
            "duration": 0.234
        },
        "model": "text-embedding-3-small",
        "provider": "openai"
    }
    ```
    """
    # Get the embedding model action
    action = await EmbeddingModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Embedding model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Validate text
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")

    # Generate embedding
    embedding = await action.embed(text)

    # Get provider name
    provider = getattr(
        action,
        "provider",
        action.get_class_name().replace("EmbeddingModelAction", "").lower(),
    )

    return {
        "embedding": embedding,
        "dimensions": len(embedding),
        "metrics": {
            "total_tokens": action.total_tokens,
            "duration": action.total_duration,
        },
        "model": action.model,
        "provider": provider,
    }


# ============================================================================
# Batch Embed Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/embed/batch",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Embedding Model Action"],
    response=success_response(
        data={
            "embeddings": ResponseField(
                field_type=List[List[float]],
                description="List of vector embeddings",
            ),
            "count": ResponseField(
                field_type=int,
                description="Number of embeddings generated",
                example=3,
            ),
            "dimensions": ResponseField(
                field_type=int,
                description="Number of dimensions in each embedding vector",
                example=1536,
            ),
            "metrics": ResponseField(
                field_type=Dict[str, Any],
                description="Aggregate query metrics",
                example={
                    "total_tokens": 60,
                    "duration": 0.567,
                },
            ),
            "model": ResponseField(
                field_type=str,
                description="Model identifier used",
                example="text-embedding-3-small",
            ),
            "provider": ResponseField(
                field_type=str,
                description="Provider name",
                example="openai",
            ),
        }
    ),
)
async def embed_batch(
    action_id: str,
    texts: List[str],
) -> Dict[str, Any]:
    """Generate vector embeddings for multiple texts in batch.

    This endpoint generates embeddings for multiple texts in a single request.
    This is more efficient than making multiple individual requests when you
    need to embed several texts.


    **Args:**

    - action_id: ID of the embedding model action to use
    - texts: List of texts to embed (each text must be non-empty)


    **Returns:**

    Dictionary containing:

    - **embeddings**: List of embedding vectors (one per input text)
    - **count**: Number of embeddings generated
    - **dimensions**: Number of dimensions in each embedding vector
    - **metrics**: Aggregate query metrics
    - **model**: Model identifier used
    - **provider**: Provider name


    **Raises:**

    - ResourceNotFoundError: If action not found
    - ValueError: If texts list is empty or contains empty strings


    **Examples:**

    Batch embedding:

    ```json
    POST /actions/abc123/embed/batch
    {
        "texts": [
            "First document text",
            "Second document text",
            "Third document text"
        ]
    }
    ```

    Response:

    ```json
    {
        "embeddings": [
            [0.1, -0.2, 0.3, ...],
            [0.2, -0.1, 0.4, ...],
            [0.3, -0.3, 0.2, ...]
        ],
        "count": 3,
        "dimensions": 1536,
        "metrics": {
            "total_tokens": 27,
            "duration": 0.567
        },
        "model": "text-embedding-3-small",
        "provider": "openai"
    }
    ```
    """
    # Get the embedding model action
    action = await EmbeddingModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Embedding model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Validate texts
    if not texts:
        raise ValueError("Texts list cannot be empty")

    if any(not text or not text.strip() for text in texts):
        raise ValueError("All texts must be non-empty")

    # Track initial metrics
    initial_requests = action.total_requests
    initial_tokens = action.total_tokens
    initial_duration = action.total_duration

    # Generate embeddings for all texts
    embeddings = []
    for text in texts:
        embedding = await action.embed(text)
        embeddings.append(embedding)

    # Calculate aggregate metrics
    requests_made = action.total_requests - initial_requests
    tokens_used = action.total_tokens - initial_tokens
    duration_used = action.total_duration - initial_duration

    # Get provider name
    provider = getattr(
        action,
        "provider",
        action.get_class_name().replace("EmbeddingModelAction", "").lower(),
    )

    # Determine dimensions from first embedding
    dimensions = len(embeddings[0]) if embeddings else 0

    return {
        "embeddings": embeddings,
        "count": len(embeddings),
        "dimensions": dimensions,
        "metrics": {
            "total_tokens": tokens_used,
            "duration": duration_used,
            "requests": requests_made,
        },
        "model": action.model,
        "provider": provider,
    }


# ============================================================================
# Metrics Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/embedding/metrics",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Embedding Model Action"],
    response=success_response(
        data={
            "total_requests": ResponseField(
                field_type=int,
                description="Total number of embedding requests made",
                example=150,
            ),
            "total_tokens": ResponseField(
                field_type=int,
                description="Cumulative token usage",
                example=45000,
            ),
            "total_cost": ResponseField(
                field_type=float,
                description="Estimated total cost in USD",
                example=0.675,
            ),
            "total_duration": ResponseField(
                field_type=float,
                description="Cumulative query duration in seconds",
                example=125.5,
            ),
            "average_duration": ResponseField(
                field_type=float,
                description="Average query duration in seconds",
                example=0.837,
            ),
            "model": ResponseField(
                field_type=str,
                description="Model identifier",
                example="text-embedding-3-small",
            ),
            "provider": ResponseField(
                field_type=str,
                description="Provider name",
                example="openai",
            ),
            "embedding_dimensions": ResponseField(
                field_type=int,
                description="Expected embedding dimensions (0 = auto-detect)",
                example=1536,
            ),
        }
    ),
)
async def get_embedding_model_action_metrics(action_id: str) -> Dict[str, Any]:
    """Get usage metrics for an embedding model action.

    Returns comprehensive usage statistics including:


    - Total embedding requests made through this action
    - Cumulative token usage
    - Estimated cost in USD based on model pricing
    - Total and average query duration
    - Expected embedding dimensions


    Metrics are accumulated across all queries and persist until reset.


    **Args:**

    - action_id: ID of the embedding model action


    **Returns:**

    Dictionary with metrics including:

    - **total_requests**: Number of embedding requests made
    - **total_tokens**: Cumulative token usage
    - **total_cost**: Estimated cost in USD
    - **total_duration**: Cumulative query time in seconds
    - **average_duration**: Average query time in seconds
    - **model**: Model identifier
    - **provider**: Provider name (openai, huggingface, openrouter, etc.)
    - **embedding_dimensions**: Expected embedding dimensions


    **Raises:**

    - ResourceNotFoundError: If action not found
    """
    action = await EmbeddingModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Embedding model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Calculate average duration
    average_duration = (
        action.total_duration / action.total_requests
        if action.total_requests > 0
        else 0.0
    )

    # Get provider name
    provider = getattr(
        action,
        "provider",
        action.get_class_name().replace("EmbeddingModelAction", "").lower(),
    )

    return {
        "total_requests": action.total_requests,
        "total_tokens": action.total_tokens,
        "total_cost": action.total_cost,
        "total_duration": action.total_duration,
        "average_duration": average_duration,
        "model": action.model,
        "provider": provider,
        "embedding_dimensions": action.embedding_dimensions,
    }
