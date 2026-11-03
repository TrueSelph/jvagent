# Database indexing and migration (jvagent)

jvagent builds on [jvspatial](https://github.com/trueselph/jvspatial) declarative indexing (`attribute`, `@compound_index`, `GraphContext.ensure_indexes`). This document covers **jvagent-only** behavior: eager startup migration and deprecated index cleanup.

## jvspatial reference

- [Declarative Database Indexing](../../jvspatial/docs/md/optimization.md#declarative-database-indexing) — when `ensure_indexes` runs, `JVSPATIAL_AUTO_CREATE_INDEXES`, backend behavior
- [Custom Database Implementation Guide](../../jvspatial/docs/md/custom-database-guide.md) — `Database.create_index`, `Database.drop_deprecated_indexes`

## What jvagent adds

### `run_index_migration()`

Defined in `jvagent.core.index_bootstrap`. Called at the start of:

- `pre_startup_bootstrap` in `jvagent.cli.server_config` (before the HTTP server accepts traffic)
- `bootstrap_only` in `jvagent.cli.commands` (`jvagent bootstrap` CLI)

It:

1. Calls `database.drop_deprecated_indexes(DEPRECATED_INDEXES)` — a small map of MongoDB collection → index names that are no longer defined in code (see `index_bootstrap.py`).
2. Clears jvspatial’s in-process `_ensured_indexes` cache so every entity class is re-evaluated.
3. Calls `ensure_indexes` for jvagent’s core entity types (Agent, Action, Conversation, Interaction, User, RepairState), plus `Edge`, auth objects (`User`, `RefreshToken`, `TokenBlacklist`, `APIKey`, `PasswordResetToken`), `DBLog`, and optional OAuth token types.
4. When `JVSPATIAL_AUTO_CREATE_INDEXES` is enabled, creates the edge and auth indexes listed below (jvspatial 0.1.1 does not declare them) and, if `JVAGENT_PAGEINDEX_DB_TYPE=mongodb`, ensures `DocumentRootNode` and `DocumentNode` indexes on the PageIndex database rather than the prime graph.

### Why

By default jvspatial creates indexes lazily on first `save` / `find` per class. Running migration at jvagent startup aligns indexes with the current code **before** user traffic, and uses the MongoDB adapter’s conflict handling (errors 85/86) plus explicit drops of known orphan index names.

### Customizing deprecated names

Edit `DEPRECATED_INDEXES` in `jvagent/core/index_bootstrap.py` when an index is removed or renamed in application code and the old name should be dropped from existing databases.

## Separation of concerns

| Layer | Responsibility |
|-------|----------------|
| **jvspatial** | `Database` API, `ensure_indexes`, MongoDB `create_index` / conflict retry, entity metadata |
| **jvagent** | When to run `run_index_migration`, which entity classes to include, `DEPRECATED_INDEXES` list for this app stack |

Other applications using jvspatial can implement their own startup hook that calls `ensure_indexes` and `drop_deprecated_indexes` without using jvagent.

## Indexes added here

PageIndex compounds are declared on the model classes (`entity` stays a top-level key; jvspatial would otherwise rewrite it to `context.entity`):

- PageIndex `node` / `pageindex_root_collection_doc`: `entity`, `context.collection_name`, `context.doc_name` (partial `entity == "DocumentRootNode"`)
- PageIndex `node` / `pageindex_node_collection_doc_node`: `entity`, `context.collection_name`, `context.doc_name`, `context.node_id` (partial `entity == "DocumentNode"`)

`_DIRECT_INDEXES` in `index_bootstrap.py` is applied with `create_index` because those classes live in jvspatial:

- `edge` / `idx_target_source`: `target`, `source`
- `object` / `auth_user_email`: `entity`, `context.email` (unique; partial `entity == "User"` and non-empty email)
- `object` / `auth_refresh_lookup`: `entity`, `context.token_lookup`, `context.is_active` (partial `entity == "RefreshToken"`)
- `object` / `auth_refresh_user_active`: `entity`, `context.user_id`, `context.is_active` (partial `entity == "RefreshToken"`)
- `object` / `auth_blacklist_token_id`: `entity`, `context.token_id` (partial `entity == "TokenBlacklist"`)
- `object` / `auth_apikey_hash`: `entity`, `context.key_hash` (partial `entity == "APIKey"`)
- `object` / `auth_password_reset_lookup`: `entity`, `context.token_lookup` (partial `entity == "PasswordResetToken"`)

Registering `Edge` also creates the unique `(source, target, entity)` index that class already declares.

## One-time migration (Lambda / Silvie)

On Lambda, `JVSPATIAL_AUTO_CREATE_INDEXES` defaults to false, so `run_index_migration()` does not create indexes during cold start. Leave it unset in the Lambda environment. Apply indexes once, from a machine that can reach the databases, with the flag turned on:

```bash
JVSPATIAL_AUTO_CREATE_INDEXES=true \
JVSPATIAL_DB_TYPE=mongodb \
JVSPATIAL_MONGODB_URI='mongodb://...' \
JVSPATIAL_MONGODB_DB_NAME=jvagent_silvie_ai \
JVAGENT_PAGEINDEX_DB_TYPE=mongodb \
JVAGENT_PAGEINDEX_DB_URI='mongodb://...' \
JVAGENT_PAGEINDEX_DB_NAME='<app_id>_pageindex_db' \
jvagent bootstrap
```

- Prime graph indexes (`node`, `edge`, `object`) are written to `jvagent_silvie_ai`.
- PageIndex indexes are written only when `JVAGENT_PAGEINDEX_DB_TYPE=mongodb`. The database name is `JVAGENT_PAGEINDEX_DB_NAME`, or `{JVAGENT_APP_ID}_pageindex_db` when that variable is unset.
- Index builds use background mode. After `bootstrap` finishes, leave `JVSPATIAL_AUTO_CREATE_INDEXES` unset (or `false`) on Lambda so later cold starts do not repeat DDL.

`find_one` without a limit, and neighbor loads that filter on `id` instead of `_id`, are in jvspatial and are not changed by this package.
