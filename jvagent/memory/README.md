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
                                          ├── [edge] ──► Interaction (Node)
                                          │                   └── [edge] ──► Artifact (Node)*
                                          └── [edge] ──► Artifacts (Node, branch)
                                                              └── [edge] ──► Artifact (Node)
```

\* An `Interaction ──► Artifact` edge marks that interaction as a *producer* of the
artifact (used for refcounted pruning). The same `Artifact` is also a child of the
conversation's single `Artifacts` branch node, which is what `get_artifacts()` queries.

### Artifacts (ADR-0021)

A **conversation-scoped artifact registry** stores durable, queryable side-products of
a turn — today, vision interpretations of uploaded images, so a later turn can
back-reference an image without re-upload. Artifacts hang off a single `Artifacts`
branch node under the `Conversation`; each `Artifact` is also connected from the
`Interaction`(s) that produced it.

- **Write**: `conversation.add_artifact(interaction, *, name, data, summary=None, tags=None, source="", kind="text", pinned=False, filename="", mime="", size=0, path="")` — creates the artifact, wires it under the `Artifacts` branch and from the producing `interaction`. The `filename`/`mime`/`size`/`path` fields describe a **file-backed** artifact (ADR-0021 S4).
- **Uploaded files (ADR-0021 S4)**: every file in `visitor.data` (keys `image_urls`, `whatsapp_media`, `files`, `attachments`, `documents`) is ingested as a `source="upload"` artifact by the orchestrator's `_ingest_uploads` reflex (gated by `ingest_uploads`, default on). The bytes are persisted to the caller's **per-user file storage** and referenced by `Artifact.path` — **never stored inline on the node** (keeps the graph lean). Text files are decoded into the queryable `data`; binaries carry a descriptor + path. An uploaded image yields *two* artifacts: the file (`source="upload"`, `kind="image"`) and its interpretation (`source="vision"`). When a file-backed artifact is reaped, `_reap_artifacts_for` also deletes its stored bytes (`_delete_artifact_file`), so storage tracks the graph.
- **Query**: `conversation.get_artifacts(*, name=None, source=None, tags=None)` — returns full `Artifact` nodes; `artifact.index_row()` is a payload-free summary row (name/source/tags/summary) for cheap listing.
- **Refcounted pruning**: when an interaction is pruned, `_reap_artifacts_for` deletes each artifact it produced **only if no other (surviving) interaction still produces it** and it is not `pinned`. Toggle with `conversation.prune_artifacts_with_interaction` (default `True`).
- **Tool surface**: the orchestrator exposes `list_artifacts` / `get_artifact` (gated by its `vision` attribute, pinned visible) so the model can read prior artifacts back. See [actions-catalog](../../.planning/reference/actions-catalog.md) (VisionAction) and ADR-0021.
- **Recall on back-reference (ADR-0021 S3)**: surfacing the tools isn't enough for a weak model. The orchestrator adds a vision-gated `artifact_recall_prompt` affordance and a deterministic seed — when a turn carries no new image but the utterance refers back to an earlier upload, the most-recent image interpretation(s) are read from the registry and seeded into the loop so recall doesn't depend on the model choosing a tool.

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
- **Vision**: image interpretations are no longer stored on the interaction. When vision is enabled the orchestrator runs a pre-loop reflex (VisionAction) and stores the description as a conversation **artifact** (see Artifacts above), reachable for follow-ups without re-sending images. (ADR-0021.)
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
