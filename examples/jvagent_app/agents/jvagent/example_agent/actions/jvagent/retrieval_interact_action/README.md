# RetrievalInteractAction

RetrievalInteractAction is a core InteractAction that retrieves relevant context from a vector store using the interaction's interpretation (or utterance as fallback) and composes a structured directive for PersonaAction to use when generating responses.

## Overview

This is a stub action that delegates to the core implementation in `jvagent.action.retrieval.retrieval_interact_action`.

## Configuration

See the main [RetrievalInteractAction documentation](../../../../../../jvagent/action/retrieval/README.md) for full configuration details.

### Example Configuration

```yaml
actions:
  - action: jvagent/retrieval_interact_action
    context:
      enabled: true
      vectorstore_action_type: "TypesenseVectorStore"
      collection: "knowledge_base"
      k: 5
      weight: -50
      min_score_threshold: 0.7
```

## Dependencies

- Requires a VectorStore action (e.g., TypesenseVectorStore) to be registered
- Requires InteractRouter to run first (for interpretation) or will fallback to utterance
- PersonaAction will consume the generated directive

