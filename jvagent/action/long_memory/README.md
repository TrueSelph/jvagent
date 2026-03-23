# UserLongMemoryInteractAction

Passive **interact action** (`jvagent/long_memory_interact_action`) that updates **graph-backed long-term memory** on the user: a `UserLongMemory` root with per-category `UserLongMemoryNode` children (markdown content per category).

Default categories include `interests`, `facts_and_preferences`, `open_threads`, and `recent_events`; the LLM may add more.

## Behaviour (summary)

1. Runs on a configurable schedule (`update_frequency`: every N user messages).
2. Loads recent history (`history_limit`).
3. Calls the configured `LanguageModelAction` with the current memory graph + history.
4. Parses JSON `category → markdown` and writes each category to its node.

## Example `agent.yaml` snippet

```yaml
actions:
  - action: jvagent/long_memory_interact_action
    context:
      enabled: true
      model: "gpt-4o"
      update_frequency: 3
      history_limit: 6
```

## Related

- **Retrieval**: use `jvagent/long_memory_retrieval_interact_action` (page-index RAG) against the same collection/metadata conventions you configure for stored memory documents.
- **`User.user_model`**: optional structured dict on the `User` node (facts/preferences API) — separate from graph long memory under `UserLongMemory` (see `jvagent.memory.user_long_memory`).

## Dependencies

- A `LanguageModelAction` (e.g. `OpenAILanguageModelAction`), referenced via `model_action_type`.
