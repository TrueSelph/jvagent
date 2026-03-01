# Memory System

The memory module provides the graph-based storage for user conversations and interactions.

## Entity Relationships

```
Memory (Node)
    └── [edge] ──► User (Node)
                      └── [edge] ──► Conversation (Node)
                                          └── [edge] ──► Interaction (Node)
```

## Key APIs

### Conversation

- **Context**: Use `conversation.context` for reads and `conversation.update_context(updates)` for persisted writes.
- **Interactions**: `add_interaction()`, `create_interaction()`, `get_interactions()`, `get_first_interaction()`, `get_last_interaction()`
- **History**: `get_interaction_history()`, `get_conversation_history()`, `get_event_history()`, `get_interpretation_history()`, `get_context_history()`
- **Tasks**: `add_active_task()`, `update_task()`, `remove_active_task()`, `get_active_tasks()`, `get_active_task()`, `get_active_tasks_for_context()`

### Interaction

- **Directives**: Access via `interaction.directives`. Use `get_unexecuted_directives()` / `get_executed_directives()` for filtered views.
- **Parameters**: Access via `interaction.parameters`. Use `get_unexecuted_parameters()` / `get_executed_parameters()` for filtered views.
- **Events**: `interaction.events`, `get_events_by_action()`
- **Response**: `set_response()`, `has_response()`, `close_interaction()`

### User

- **Conversations**: `create_conversation()`, `get_conversation_by_session()`, `list_conversations()`, `get_active_conversation()`
- **Profile**: `set_name()`, `set_display_name()`, `get_display_name()`, `update_user_model()`, `get_user_model()`

### Memory (Manager)

- **Users**: `get_user()`, `get_users()`, `get_user_by_session()`
- **Sessions**: `get_session()`, `get_conversation_by_session()`
- **Admin**: `purge_user_memory()`, `purge_conversations()`, `repair_memory()`, `export_memory()`, `memory_healthcheck()`

## See Also

- [Task Tracking](../../docs/task-tracking.md) - Conversation task API
- [InteractAction API](../action/interact/README.md) - Interaction flow and response format
