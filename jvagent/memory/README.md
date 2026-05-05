# Memory System

The memory module provides the graph-based storage for user conversations and interactions.

## REST Endpoints

| Method | Path | Auth | Description |
|--------|------|------|--------------|
| GET | `/api/agents/{agent_id}/memory/users` | Admin | List User nodes with pagination. Query: `filter` (MongoDB-style JSON, e.g. `{"context.user_id":{"$in":["id1","id2"]}}`), `page`, `page_size`. Returns full User records (id, entity, context). |
| DELETE | `/api/agents/{agent_id}/memory/purge` | Admin | Purge conversations (query: `user_id`, `conversation_id`). Cascade deletes interactions. Does not delete User nodes. |
| DELETE | `/api/agents/{agent_id}/memory/users/{user_id}` | Admin | Delete a user node and all connected nodes (Conversations, Interactions, SubscriptionSettings, etc.). |
| POST | `/api/agents/{agent_id}/memory/repair` | Admin | Repair orphaned nodes, dual edges, and missing conversation-to-first-interaction edges for a single agent (query: `recent_minutes`). |

### App-Wide Repair

`POST /graph/repair` is the recommended entry point for maintenance. It runs **memory repair for all agents first** (before any graph repair), then performs full structural graph repair (dead edges, orphaned nodes, duplicate edges). Query parameters: `dry_run`, `recent_minutes`, `max_seconds`. For large graphs, keep calling the same endpoint until `status` becomes `completed`; the server persists progress in a temporary `RepairState` node attached to `App`, and clears it automatically when the repair completes.

Use `/api/agents/{agent_id}/memory/repair` only when you need to target a single agent's memory in isolation.

**Note:** Endpoints marked Admin require the `admin` role. This includes `GET /api/agents/{agent_id}/memory/users`. The self-memory endpoint (`GET /api/agents/{agent_id}/memory/me`) requires authentication and resolves the caller's `user_id` from auth context.

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
- **Tasks (read)**: `get_tasks()`, `get_task()`, `get_active_tasks_for_context()`. All task writes go through `TaskStore` (see `docs/task-tracking.md`).

### Interaction

- **Directives**: Access via `interaction.directives`. Use `get_unexecuted_directives()` / `get_executed_directives()` for filtered views.
- **Parameters**: Access via `interaction.parameters`. Use `get_unexecuted_parameters()` / `get_executed_parameters()` for filtered views.
- **Events**: `interaction.events`, `get_events_by_action()`
- **Response**: `set_response()`, `has_response()`, `close_interaction()`
- **Image interpretation**: `image_interpretation` — Extensive AI description of attached images (generated behind the scenes when vision is enabled). Used for follow-up questions without re-sending images.
- **Routing (from InteractRouter)**: `interpretation`, `anchors`, `intent_type`, `response_posture` (RESPOND | SUPPRESS | DEFER)

### User

- **Conversations**: `create_conversation()`, `get_conversation_by_session()`, `list_conversations()`, `get_active_conversation()`
- **Profile**: `set_name()`, `set_display_name()`, `get_display_name()`, `update_user_model()`, `get_user_model()`

### Memory (Manager)

- **Users**: `get_user()`, `get_users()`, `get_user_by_session()`
- **Sessions**: `get_session()`, `get_conversation_by_session()`
- **Admin**: `purge_user_memory()`, `purge_conversations()`, `repair_memory()`, `export_memory()`, `memory_healthcheck()`

### Interaction rolling limit (pruning)

- **On append**: When a new interaction is added and `interaction_count` exceeds `interaction_limit`, pruning runs inside `Conversation.add_interaction` / `_prune_old_interactions`. Per-call work is capped by env `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default `100`) so latency stays bounded if the graph is far over limit.
- **On session resume**: `get_session()` does **not** prune when re-opening an existing conversation (cases: session_id only, or user_id + session_id). That keeps interact startup fast as history grows.
- **Bulk maintenance**: Call `Memory.apply_interaction_limit_pruning_for_connected_users()` from a scheduled job or admin repair path to sync limits and prune across users when needed.

## See Also

- [Task Tracking](../../docs/task-tracking.md) - Conversation task API
- [InteractAction API](../action/interact/README.md) - Interaction flow and response format
