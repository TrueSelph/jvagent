# TypesenseVectorStore

TypesenseVectorStore is a VectorStore implementation that uses Typesense for vector storage and semantic search.

## Overview

This is a stub action that delegates to the core implementation in `jvagent.action.vectorstore.typesense`.

## Configuration

### Example Configuration

```yaml
actions:
  - action: jvagent/typesense_vectorstore
    context:
      enabled: true
      host: localhost
      port: 8108
      protocol: http
      api_key: ${TYPESENSE_API_KEY}
      connection_timeout_seconds: 2
      embedding_dimensions: 384
      embedder_type: sentence-transformers
      default_collection: default
```

## Dependencies

- Requires `typesense` Python package: `pip install typesense`
- Requires an embedding model (sentence-transformers or OpenAI)

## Usage

TypesenseVectorStore provides semantic search capabilities for:
- Knowledge base retrieval
- Document similarity search
- Context retrieval for RetrievalInteractAction

See the main TypesenseVectorStore implementation for full API documentation.

