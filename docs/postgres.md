# Running jvagent on PostgreSQL

> **Status: blocked upstream.** jvagent itself needs no changes to run on Postgres, and jvspatial ships a complete `PostgresDB` backend — but the code path `jvagent` uses to build its `Server` does not accept `db_type=postgres`. Two upstream gaps must be fixed in jvspatial first; both are described in [§3](#3-upstream-blockers). Until then, use `json`, `sqlite`, `mongodb`, or `dynamodb`.
>
> Verified against `jvspatial==0.0.15` (the pin in [`pyproject.toml`](../pyproject.toml)), and reproduced identically on 0.0.9 and 0.0.12.

---

## 1. Configuration surface

Postgres is selected the same way as every other backend — `JVSPATIAL_DB_TYPE`, resolved with the usual precedence (env > `app.yaml` > default; see [configuration.md](configuration.md)).

| Key | Purpose | Default |
|---|---|---|
| `JVSPATIAL_DB_TYPE` | `postgres` (alias: `postgresql`) | `json` |
| `JVSPATIAL_POSTGRES_DSN` | libpq DSN, e.g. `postgresql://user:pass@host:5432/dbname` | `postgresql://postgres:postgres@localhost:5432/jvdb` |
| `JVSPATIAL_POSTGRES_MIN_POOL_SIZE` | asyncpg pool floor | 0 (serverless) / 2 |
| `JVSPATIAL_POSTGRES_MAX_POOL_SIZE` | asyncpg pool ceiling | 3 (serverless) / 10 |
| `JVSPATIAL_POSTGRES_POOLER_MODE` | `session` or `transaction` — set `transaction` behind PgBouncer / RDS Proxy (disables asyncpg's prepared-statement cache) | `session` |

Notes:

- **The driver is an extra.** `pip install asyncpg` (or `jvspatial[postgres]`); it is not pulled in by jvagent's base dependencies.
- **`JVSPATIAL_DB_PATH` does not apply.** [`server_config.py:161`](../jvagent/cli/server_config.py) only exports a path for `json` / `sqlite`.
- **The DSN reaches the driver via env only.** [`server_config.py:126-164`](../jvagent/cli/server_config.py) threads a connection string into jvspatial's `DatabaseConfig` for `mongodb` and table/region for `dynamodb`; there is no Postgres field, so `JVSPATIAL_POSTGRES_DSN` must be set in the environment — an `app.yaml` `database.uri` will **not** be picked up.
- **Logging DB has no Postgres branch.** jvspatial's `logging/config.py` falls through to a `json` file log for any unrecognized type — silently. Set `JVSPATIAL_LOG_DB_TYPE=json` (or `mongodb`) explicitly so the fallback is a decision rather than a surprise. See [logging.md](logging.md).
- **PageIndex is a separate store** with its own `JVAGENT_PAGEINDEX_DB_TYPE` (`json` by default) and is unaffected by the main graph backend.

Schema is created by the driver on first use — three tables (`node`, `edge`, `object`) plus indexes. Nothing to migrate by hand.

---

## 2. Local setup

```bash
docker run -d --name jvagent-pg \
  -e POSTGRES_USER=jvagent -e POSTGRES_PASSWORD=jvagent -e POSTGRES_DB=jvagent_demo \
  -p 55432:5432 postgres:16-alpine
```

```bash
pip install asyncpg
```

Then in your app's `.env` (see [`.env.example`](../.env.example)):

```bash
JVSPATIAL_DB_TYPE=postgres
JVSPATIAL_POSTGRES_DSN=postgresql://jvagent:jvagent@localhost:55432/jvagent_demo
JVSPATIAL_LOG_DB_TYPE=json
```

```bash
jvagent examples/jvagent_app bootstrap
```

As of `jvspatial==0.0.15` that bootstrap **fails**:

```
❌ Failed to initialize GraphContext: Unsupported database type: postgres
ValueError: Unsupported database type: postgres
```

---

## 3. Upstream blockers

Both live in jvspatial. Per [`CLAUDE.md`](../CLAUDE.md) §4 and [`adr/0006`](../.planning/adr/0006-jvspatial-dependency.md), database adapter behavior is jvspatial's to own — do **not** work around either of these inside jvagent.

### 3.1 `Server` path rejects `postgres`

`jvspatial/api/components/database_configurator.py:177` — `DatabaseConfigurator.initialize_graph_context()` dispatches on `db_type` through a hard-coded `json` / `mongodb` / `sqlite` / `dynamodb` if-chain and raises `ValueError: Unsupported database type: {db_type}` for anything else.

This is the path `Server(...)` takes, and therefore the path [`create_server_from_config()`](../jvagent/cli/server_config.py) takes. The layer beneath it is complete: `jvspatial/db/factory.py` handles `("postgres", "postgresql")`, reads `JVSPATIAL_POSTGRES_*` from env, and returns a fully functional `PostgresDB`. Calling `create_database("postgres")` directly and driving `Root` / `Node` CRUD against Postgres works today.

**Fix:** add the missing `elif db_type in ("postgres", "postgresql")` branch to `initialize_graph_context()`, delegating to `create_database`.

### 3.2 asyncpg pool has no event-loop affinity

`jvspatial/db/postgres.py:353` — `PostgresDB._ensure_pool()` memoizes `self._pool` (created under `self._pool_lock`, itself constructed in `__init__`) and never revalidates which event loop that pool belongs to.

This breaks jvagent specifically, because jvagent boots across **two** loops: the CLI bootstraps the application graph inside `asyncio.run(...)` ([`cli/main.py`](../jvagent/cli/main.py)), then hands off to uvicorn, which runs its own loop. The pool created during bootstrap stays bound to the first, now-dead loop, and the first query on the server loop fails:

```
❌ Database initialization failed: cannot perform operation: another operation is in progress
asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation
```

File-based backends never notice; this is the same class of bug as the per-event-loop lock pattern jvagent already uses at [`core/app.py:100-124`](../jvagent/core/app.py).

**Fix:** give `_ensure_pool()` loop affinity — record the loop the pool was created on, and when the running loop differs, drop the stale pool and its lock and rebuild.

---

## 4. What was verified

With both gaps patched at runtime (monkeypatch only — no jvagent or jvspatial source changed), against `jvspatial==0.0.15` + `asyncpg==0.31.0` + `postgres:16-alpine`:

- `jvagent examples/jvagent_app bootstrap` completed; `App`, `Agents`, both example agents, and every installed action persisted to Postgres — 44 `node` rows, 43 `edge` rows, 3 `object` rows after bootstrap plus two conversation turns, with the driver creating all three tables and their indexes unattended.
- Server started clean — `/health` reported `"database":"connected"`, lifecycle logged `📊 Database: PostgresDB | 🌳 Root: n.Root.root`.
- Admin bootstrap wrote a `User` to Postgres; `POST /api/auth/login` returned a JWT against it.
- `POST /api/agents/{id}/interact` ran full turns on the orchestrator example agent (`OrchestratorInteractAction` → `ReplyAction`), creating `User` / `Conversation` / `Interaction` nodes.
- Conversation state survived a **full server restart** — a later turn recalled a value stated before the restart, read back out of Postgres rather than process memory.

So the blockers are strictly at the configuration boundary. Once jvspatial accepts `postgres` in the `Server` path and makes its pool loop-aware, jvagent runs on Postgres as-is.

---

## 5. Related documentation

- [configuration.md](configuration.md) — config precedence and the `app.yaml` / env split.
- [environment-keys-reference.md](environment-keys-reference.md) — canonical env key inventory.
- [database-indexing.md](database-indexing.md) — `ensure_indexes`, `JVSPATIAL_AUTO_CREATE_INDEXES`, per-backend behavior.
- [logging.md](logging.md) — log DB selection and retention.
- [`.planning/reference/jvspatial-integration.md`](../.planning/reference/jvspatial-integration.md) §2.5 — the backend matrix and the jvagent/jvspatial boundary.
