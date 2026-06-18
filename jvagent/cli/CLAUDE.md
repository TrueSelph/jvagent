# jvagent/cli/ — Agent Guide

> Local guide for the CLI and server bootstrap. Cross-link: root [`/CLAUDE.md`](../../CLAUDE.md), [`/.planning/runbooks/local-dev.md`](../../.planning/runbooks/local-dev.md), [`/docs/scaffolding.md`](../../docs/scaffolding.md).

---

## 1. What this directory owns

- The `jvagent` and `python -m jvagent` entry points.
- Argument parsing, app-root extraction, flag handling.
- Subcommand dispatch: `status`, `agent`, `action`, `skill`, `bootstrap`, `bundle`, `app`, `validate`, `stress-seed`.
- Server bootstrap from `app.yaml` (`create_server_from_config`).
- Graph bootstrap (`bootstrap_application_graph`).

It does **not** own: the actual graph node definitions (that's `core/`), HTTP server runtime (that's jvspatial).

---

## 2. Key files

| File | Purpose |
|---|---|
| `__init__.py` | Exports `main` |
| `main.py:118-244` | `main()` — top-level entry; parses args, dispatches |
| `main.py:58-115` | `_first_app_root_path()` — extracts app root from arg list |
| `main.py:142-149` | `--serverless` flag handling |
| `main.py:152-159` | `--debug` flag |
| `main.py:161-179` | `--update` / `--source` / `--merge` parsing (mutually exclusive rules) |
| `main.py:181-192` | `--purge` (dev-only) |
| `main.py:205-226` | Subcommand dispatch |
| `main.py:239-244` | Default → `run_server()` |
| `commands.py` | `run_server`, `bootstrap_only`, `handle_*_command`, `purge_app_data`, `show_status`, `print_usage`, `run_validate`, `load_app_env` |
| `server_config.py:59-180` | `create_server_from_config()` — reads app.yaml + env, builds jvspatial Server |
| `server_config.py` | `_set_db_env_from_config`, `_import_core_endpoint_modules` |
| `bootstrap.py` | `bootstrap_application_graph()` — orchestrates App + Agents + Actions creation |
| `app_commands.py` | `jvagent app create / profile new / ...` subcommands |
| `__main__.py:5-6` | `python -m jvagent` → `cli.main:main()` |

---

## 3. CLI shape (memorize)

```
jvagent [app_root] [SUBCOMMAND] [FLAGS]

DEFAULT (no subcommand) → start HTTP server

SUBCOMMANDS:
  status               diagnostic snapshot of an app's graph
  agent <op> ...       agent CRUD (add, list, enable, disable, delete)
  action <op> ...      action CRUD on an agent
  skill <op> ...       skill management
  bootstrap            bootstrap graph then exit (no server)
  bundle <app_root>    emit Dockerfile + deployment artifacts
  app create|profile   scaffold a new app or profile
  validate             validate app.yaml + agents
  chat                 serve the bundled jvchat UI on its own port (jvagent/webui)
  stress-seed          generate synthetic graph for testing

FLAGS (apply to default + bootstrap):
  --debug              verbose logging
  --update             merge-mode YAML sync
  --update --source    destructive YAML sync (DESTRUCTIVE)
  --update --merge     explicit merge (== --update alone)
  --purge              wipe DB (dev only; requires JVSPATIAL_ENVIRONMENT=development)
  --serverless         single-worker, SERVERLESS_MODE=true
```

`--source` and `--merge` REQUIRE `--update`. They are mutually exclusive ([`main.py:167-172`](main.py)).

---

## 4. Contracts (don't break)

1. **App root resolution** ([`main.py:58-115`](main.py)) — strips path tokens, keeps subcommands + flags + stress-seed args. Don't break this for `jvagent ./myapp agent add ...`.
2. **`--purge` MUST require dev mode** ([`main.py:185-190`](main.py)). Never relax.
3. **`bootstrap_only` and `run_server` reset `App.update_mode` to `run`** after a successful sync. Don't remove this; otherwise one-shot ops persist across restarts.
4. **`load_app_env(app_root)` runs first** ([`main.py:130`](main.py)). Anything reading env vars before this is incorrect.
5. **`set_app_root(app_root)` must run before any node lookup** ([`main.py:134`](main.py)). Cache/config keys depend on it.
6. **`_set_db_env_from_config(app_root)` ([`main.py:150`](main.py))** translates app.yaml DB stanza into env vars jvspatial reads. Must precede any DB call.

---

## 5. Subcommand handler conventions

Each `handle_*_command` in `commands.py`:

- Receives `args` (post-subcommand) and `app_root`.
- Uses `asyncio.run(...)` for async operations.
- Exits with non-zero on errors via `sys.exit(N)`.
- Prints user-facing output to stdout/stderr (no return values).

When adding a subcommand:

1. Add the name to `DISPATCH` ([`main.py:42-55`](main.py)).
2. Add a dispatch branch in the `if args[0] in DISPATCH:` block ([`main.py:205-226`](main.py)).
3. Add the handler in `commands.py` (or a new sibling module).
4. Document in [`/.planning/runbooks/`](../../.planning/runbooks/) if non-trivial.

---

## 6. Tests

- `tests/cli/` — argparse/dispatch tests.
- `tests/test_env_load.py` — `load_app_env` precedence.
- `tests/scaffold/` — `app create` flow.

```bash
pytest tests/cli/ tests/test_env_load.py -v
```

---

## 7. Traps specific to cli/

| Trap | Fix |
|---|---|
| Treating a `STRESS_FLAG_NAMES` value as a path | `_first_app_root_path()` handles this — don't bypass it |
| Forgetting to strip a flag from `args` after parsing | The default-server path will see it and error "Unknown argument" |
| Calling jvspatial before `set_app_root()` + `load_app_env()` | Wrong DB / paths |
| Running `--update --source` with no app root | App root defaults to `cwd`; destructive op on the wrong dir. Always pass explicit path for source mode. |
| Mixing `--purge` with `--source` | `--purge` deletes the DB, then `--source` rebuilds — works, but slow. Use `--update --source` alone to overwrite. |

---

## 8. Don't touch from outside cli/

- The argument-parsing semantics — many runbooks and docs encode them.
- The dispatch table — subcommand name is part of the public CLI contract.
- The `update_mode` reset behavior — its absence makes cold starts unpredictable.

---

## 9. Out of scope here

- Server runtime (uvicorn + FastAPI internals): jvspatial.
- Endpoint registration: handled by jvspatial server based on imported endpoint modules.
- Graph repair: see `jvagent/core/graph_repair*.py` and `core/CLAUDE.md`.
