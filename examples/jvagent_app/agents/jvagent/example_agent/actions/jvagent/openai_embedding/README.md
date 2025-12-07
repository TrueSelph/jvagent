# OpenAI Embedding Model Action

The OpenAI Embedding Model Action provides integration with OpenAI's Embeddings API for generating vector embeddings from text.

## Overview

This action provides:
- **Vector Embeddings**: Generate dense numerical representations of text
- **Multiple Models**: Support for text-embedding-3-small, text-embedding-3-large, and text-embedding-ada-002
- **Auto-Dimension Detection**: Automatically detects embedding dimensions from the model
- **Metrics Tracking**: Tracks usage, tokens, and duration

## Configuration

The action can be configured in `agent.yaml`:

```yaml
actions:
  - action: jvagent/openai_embedding
    context:
      enabled: true
      # API Configuration
      api_key: ${OPENAI_API_KEY}  # Set in .env or environment
      api_endpoint: "https://api.openai.com/v1"
      # Model Configuration
      model: text-embedding-3-small  # Default embedding model
      embedding_dimensions: 1536  # Expected dimensions (0 = auto-detect)
      timeout: 30
```

## Attributes

- **api_key**: OpenAI API key (from environment or config)
- **api_endpoint**: API endpoint URL (defaults to https://api.openai.com/v1)
- **model**: Model identifier (e.g., 'text-embedding-3-small', 'text-embedding-ada-002')
- **embedding_dimensions**: Expected dimensions (0 = auto-detect from model)
- **timeout**: Request timeout in seconds

## Usage

### Programmatic Usage

```python
# Get the embedding model action
embedding_model = await OpenAIEmbeddingModelAction.get(action_id)

# Generate embedding
vector = await embedding_model.embed("Hello world")
print(f"Embedding dimensions: {len(vector)}")
```

### API Endpoints

The action exposes the following HTTP endpoints:

**Generate Embedding:**
```http
POST /actions/{action_id}/embed
Content-Type: application/json

{
  "text": "The quick brown fox jumps over the lazy dog"
}
```

**Batch Embedding:**
```http
POST /actions/{action_id}/embed/batch
Content-Type: application/json

{
  "texts": [
    "First document text",
    "Second document text"
  ]
}
```

**Get Metrics:**
```http
GET /actions/{action_id}/embedding/metrics
```

## Integration with Vector Stores

This embedding model action can be used with vector stores like TypesenseVectorStore:

```yaml
actions:
  - action: jvagent/openai_embedding
    context:
      enabled: true
      api_key: ${OPENAI_API_KEY}
      model: text-embedding-3-small
  
  - action: jvagent/typesense_vectorstore
    context:
      enabled: true
      embedding_model_action_type: "OpenAIEmbeddingModelAction"
      embedding_dimensions: 1536
```

## Supported Models

- **text-embedding-3-small**: 1536 dimensions (default)
- **text-embedding-3-large**: 3072 dimensions
- **text-embedding-ada-002**: 1536 dimensions (legacy)

## Examples

See the main agent README for complete examples of using embedding models with vector stores and retrieval actions.

