# UserLongMemoryStoreInteractAction

`UserLongMemoryStoreInteractAction` is an `InteractAction` that acts as a background worker to assimilate the user's Long-Term Memory into the PageIndex for semantic and tree-based document search.

## Overview

Working in tandem with `UserLongMemoryInteractAction`, this action is responsible for taking memory nodes that have been recently updated and indexing their combined markdown into a unified `user_long_memory_{user_id}` document within PageIndex. It:

1. Runs automatically in the background (post-response).
2. Looks for `UserLongMemoryNode` objects with the `needs_indexing` flag set to `True`.
3. Compiles the entire long memory graph into a single markdown document.
4. Generates an LLM-based summary and assimilates the markdown into the vectorless PageIndex.
5. Clears the `needs_indexing` flag so it only runs when necessary.

## How It Works

### Execution Flow

1. **Triggered Automatically**: The action runs asynchronously after the response is sent to the user.
2. **Checks for Updates**: It queries the `UserLongMemory` subgraph for any category nodes that have been recently updated.
3. **Short-Circuit**: If there are no nodes with `needs_indexing=True`, the action simply exits with almost zero overhead.
4. **Markdown Generation**: If updates exist, it reads the current state of all memory categories and converts them into a combined markdown format.
5. **Assimilates into PageIndex**: Any previous memory documents for the user are deleted, and the new compiled markdown is stored via `_do_assimilate`, making it available for tree search and retrieval.
6. **Clears Flags**: Resets the `needs_indexing` flag on the modified nodes.

### Key Features

- **Decoupled Architecture**: Keeps the slower process of semantic assimilation separated from the core memory analysis step.
- **Incremental Efficiency**: Since it only triggers assimilation if nodes were specifically marked as `needs_indexing=True` by the `long_memory_interact_action`, it saves database and LLM generation costs natively.
- **Idempotent Storage**: Prevents clutter by securely clearing out old iterations of the user's profile inside the `LongTermMemory` collection and rebuilding a fresh overview.

## Configuration

### Properties

- `model`: LLM model to use for assimilation and node-summary generation (default: "gpt-4o")
- `collection`: The PageIndex collection to use (default: "LongTermMemory")
- `weight`: Execution weight ensures it runs *after* memory extraction updates (default: 160)
- `always_execute`: Always run regardless of routine routing rules (default: true)
- `run_in_background`: Executed after API response to avoid blocking user latency (default: true)

### Example Configuration

```yaml
actions:
  - action: jvagent/long_memory_store_interact_action
    context:
      enabled: true
      model: "gpt-4o"
      weight: 160
      collection: "LongTermMemory"
```

## Dependencies

- Depends on `UserLongMemoryInteractAction` to write the initial memory updates and set the `needs_indexing` boolean on graph nodes.
- Requires semantic indexing endpoints from `jvagent/action/pageindex` (specifically `_do_assimilate`).
