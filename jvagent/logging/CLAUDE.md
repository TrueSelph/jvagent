# jvagent/logging/ — Agent Guide

> Local guide for the logging subsystem. Cross-link: [`/.planning/observability.md`](../../.planning/observability.md), [`/docs/logging.md`](../../docs/logging.md), [`/docs/error-logging.md`](../../docs/error-logging.md), [`/docs/interaction-logging.md`](../../docs/interaction-logging.md).

---

## 1. What this directory owns

- The custom `INTERACTION` log level registration.
- The `GET /logs/agents/{agent_id}` query endpoint.
- Integration with jvspatial's logging service (separate `logs` database).

It does **not** own: per-interaction observability metrics (those live on `Interaction.observability_metrics` — see `memory/CLAUDE.md`), the actual log storage (jvspatial), or the bootstrap-startup log counter (that's in `core/`).

---

## 2. Key files

| File | Purpose |
|---|---|
| `__init__.py` | Module init |
| `service.py` | Registers the `INTERACTION` log level + jvspatial logging service bridge |
| `endpoints.py:39-113` | `GET /logs/agents/{agent_id}` — filter by agent_id, time range, query |

---

## 3. Logging tiers

| Tier | Where | Storage |
|---|---|---|
| Standard Python `logging` | `logger = logging.getLogger(__name__)` everywhere | stderr (configurable via jvspatial `configure_standard_logging`) |
| `INTERACTION` level events | Auto-recorded during walker traversal | `logs` DB (separate from main jvspatial DB) |
| Per-interaction observability metrics | `Interaction.observability_metrics` (dict) | Main DB on the Interaction node |
| Per-interaction usage tally | `Interaction.usage` | Main DB on the Interaction node |
| Per-interaction action/event/directive lists | `Interaction.actions`, `Interaction.events`, `Interaction.directives` | Main DB |
| Bootstrap warnings / errors | `core/bootstrap_logger.py:BootstrapLogger` context manager | stderr + `core/startup.py` counter |
| `DBLogHandler` errors | Auto-routed by handler | `logs` DB |

---

## 4. Contracts

1. **Logs DB is separate from main DB.** Always: `get_logging_service(database_name="logs")`. Don't accidentally use the main jvspatial context.
2. **`INTERACTION` level must be registered before any module that emits at that level imports.** `service.py` handles this; don't reorder.
3. **`Interaction.observability_metrics` is the per-interaction aggregator.** Other code SHOULD merge events into it rather than emit standalone log records, so they're recoverable per interaction.
4. **Log retention is configured via `App.log_retention_days`** ([`core/app.py:63`](../core/app.py)) — default 60 days. Long-running deploys should set this lower to bound storage.

---

## 5. Adding to this directory

| If you're adding... | Where |
|---|---|
| A new query/filter on logs | `endpoints.py` — add a query param + filter clause |
| A new log level | `service.py` — register before imports |
| A new observability field on Interaction | `memory/interaction.py` + agree on the schema in `observability_metrics` |
| A separate logging DB | Don't. Use the existing `logs` DB or extend jvspatial. |

---

## 6. Tests

- Add tests under `tests/logging/` (create if missing).
- `tests/unit/` and `tests/integration/` may have related coverage.

```bash
pytest tests/logging/ -v   # if exists
```

---

## 7. Traps specific to logging/

| Trap | Fix |
|---|---|
| Forgetting `preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"]` when reconfiguring logging | Custom handlers drop; logs go to stderr only. See `cli/main.py:34`. |
| Querying logs DB with main jvspatial context | Misses. Use `get_logging_service(database_name="logs")`. |
| Emitting massive JSON blobs in log messages | DB pressure; storage growth. Use `observability_metrics` for structured per-interaction data. |
| Setting `App.log_retention_days = 0` | Logs purged immediately. Use a reasonable value or disable retention by leaving large. |
| Logging inside `_run_background_actions` without try/except | Failures propagate. The wrapper already catches; don't double-handle. |

---

## 8. Don't touch from outside logging/

- The `INTERACTION` level integer value — third-party log consumers depend on it.
- The `logs` DB name string — endpoint contract.
- `DBLogHandler` class name — listed in `preserve_handler_class_names` at boot.

---

## 9. Out of scope here

- jvspatial's logging service internals.
- Per-action error reporting policy (each action handles its own try/except + logger.error).
- Tracing / OpenTelemetry — not currently integrated.
