# Database indexing and migration (jvagent)

jvagent builds on [jvspatial](https://github.com/trueselph/jvspatial) declarative indexing (`attribute`, `@compound_index`, `GraphContext.ensure_indexes`). This document covers **jvagent-only** behavior: eager startup migration and deprecated index cleanup.

## jvspatial reference

- [Declarative Database Indexing](https://github.com/TrueSelph/jvspatial/blob/main/docs/md/optimization.md#declarative-database-indexing) — when `ensure_indexes` runs, `JVSPATIAL_AUTO_CREATE_INDEXES`, backend behavior
- [Custom Database Implementation Guide](https://github.com/TrueSelph/jvspatial/blob/main/docs/md/custom-database-guide.md) — `Database.create_index`, `Database.drop_deprecated_indexes`

## What jvagent adds

### `run_index_migration()`

Defined in `jvagent.core.index_bootstrap`. Called at the start of:

- `pre_startup_bootstrap` in `jvagent.cli.server_config` (before the HTTP server accepts traffic)
- `bootstrap_only` in `jvagent.cli.commands` (`jvagent bootstrap` CLI)

It:

1. Calls `database.drop_deprecated_indexes(DEPRECATED_INDEXES)` — a small map of MongoDB collection → index names that are no longer defined in code (see `index_bootstrap.py`).
2. Clears jvspatial’s in-process `_ensured_indexes` cache so every entity class is re-evaluated.
3. Calls `ensure_indexes` for jvagent’s core entity types (Agent, Action, Conversation, Interaction, User, RepairState, `DBLog`, optional OAuth token types).

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
