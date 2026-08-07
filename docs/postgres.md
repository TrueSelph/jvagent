# Running jvagent on PostgreSQL

> **Requires `jvspatial >= 0.0.16`** (the pin in [`pyproject.toml`](../pyproject.toml)). On 0.0.15 and earlier, starting jvagent with `JVSPATIAL_DB_TYPE=postgres` fails outright — see [§3](#3-version-requirement).
>
> jvagent needs no Postgres-specific code: the backend and every connection setting live in jvspatial. Verified end to end against the released 0.0.16 wheel — see [§4](#4-verifying-your-setup).

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
- **`app.yaml` works too.** Every key above also resolves from the `config.database` stanza, with the usual precedence (env wins):

  ```yaml
  config:
    database:
      type: postgres
      uri: ${JVSPATIAL_POSTGRES_DSN}   # or a literal DSN, but keep credentials in env
      pooler_mode: transaction
      min_pool_size: 2
      max_pool_size: 10
  ```

  `database.uri` is shared with mongodb — `database.type` decides how it is read. A non-integer pool size is logged and ignored rather than taking the server down at startup.
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
jvagent examples/jvagent_app
```

The driver creates its schema on first use, so there is no migration step between those two commands.

---

## 3. Version requirement

Postgres works from `jvspatial >= 0.0.16`. On **0.0.15 and earlier**, `Server` construction fails before the app ever boots:

```
❌ Failed to initialize GraphContext: Unsupported database type: postgres
ValueError: Unsupported database type: postgres
```

Two defects had to be fixed upstream, both shipped in 0.0.16 ([TrueSelph/jvspatial#35](https://github.com/TrueSelph/jvspatial/pull/35)). They are worth knowing about because the second one is invisible until a restart:

1. **`Server` rejected the type.** `DatabaseConfigurator.initialize_graph_context()` dispatched `db_type` through a hard-coded `json`/`mongodb`/`sqlite`/`dynamodb` chain. The backend underneath always worked — `create_database("postgres", ...)` was fine — so only the `Server` path, the one [`create_server_from_config()`](../jvagent/cli/server_config.py) uses, was closed.

2. **The asyncpg pool had no event-loop affinity.** `PostgresDB._ensure_pool()` memoized the pool and its lock for the life of the instance. This bites jvagent specifically, because jvagent boots across **two** loops: the CLI bootstraps the graph inside `asyncio.run(...)` ([`cli/main.py`](../jvagent/cli/main.py)), then hands off to uvicorn's own loop. The pool built during bootstrap stayed bound to the first, now-dead loop, and the first query on the server loop died with:

   ```
   cannot perform operation: another operation is in progress
   asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation
   ```

   File-backed adapters never notice this. It is the same class of bug as the per-event-loop lock pattern jvagent already uses at [`core/app.py:100-124`](../jvagent/core/app.py).

Per [`CLAUDE.md`](../CLAUDE.md) §4 and [`adr/0006`](../.planning/adr/0006-jvspatial-dependency.md), database adapter behavior is jvspatial's to own — if Postgres misbehaves, fix it there rather than working around it in jvagent.

---

## 4. Verifying your setup

[`scripts/smoke_postgres.sh`](../scripts/smoke_postgres.sh) drives a real app against a real database and checks what unit tests cannot:

```bash
scripts/smoke_postgres.sh                       # spins up its own container
scripts/smoke_postgres.sh path/to/your_app      # against your app
```

It bootstraps the graph, serves it, authenticates, runs an agent turn, **restarts the server**, and reads the conversation back. The restart is the point: it is the only step that exercises pool loop-affinity, and it is what failed before 0.0.16. Agent-turn checks are skipped when no model key is configured; the persistence checks still run.

Against the released `jvspatial==0.0.16` with `asyncpg==0.31.0` and `postgres:16-alpine`, all 15 checks pass on `examples/jvagent_app`: the full graph persists (39 nodes at bootstrap, 44 nodes / 43 edges / 3 objects after two turns), `/health` reports `"database":"connected"`, lifecycle logs `📊 Database: PostgresDB`, JWT login resolves a Postgres-stored user, and a post-restart turn recalls a value stated before the restart — read back out of Postgres, not process memory.

---

## 5. Related documentation

- [configuration.md](configuration.md) — config precedence and the `app.yaml` / env split.
- [environment-keys-reference.md](environment-keys-reference.md) — canonical env key inventory.
- [database-indexing.md](database-indexing.md) — `ensure_indexes`, `JVSPATIAL_AUTO_CREATE_INDEXES`, per-backend behavior.
- [logging.md](logging.md) — log DB selection and retention.
- [`.planning/reference/jvspatial-integration.md`](../.planning/reference/jvspatial-integration.md) §2.5 — the backend matrix and the jvagent/jvspatial boundary.
