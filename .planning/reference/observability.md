# Observability — Index

> Where logs / metrics / traces live in jvagent, and which existing doc covers each. This is an **index**, not a replacement — full detail stays in the linked docs.

---

## 1. Quick map

| Concern | Where it lives | Doc |
|---|---|---|
| Standard Python logs | stderr (configurable) | [`/docs/logging.md`](../../docs/logging.md) |
| Bootstrap warnings / errors | aggregated by `StartupLogCounter` ([`core/bootstrap_logger.py`](../../jvagent/core/bootstrap_logger.py)) | [`/docs/logging.md`](../../docs/logging.md) |
| Per-interaction events (turn-level) | `INTERACTION` level → separate `logs` DB | [`/docs/interaction-logging.md`](../../docs/interaction-logging.md) |
| Per-interaction error rollup | `Interaction.observability_metrics` (dict on the Interaction node) | [`/docs/error-logging.md`](../../docs/error-logging.md) |
| Token usage + model-call tallies | `Interaction.usage` (dict on the Interaction node) | [`/docs/interaction-logging.md`](../../docs/interaction-logging.md) |
| HTTP query API for logs | `GET /logs/agents/{agent_id}` ([`jvagent/logging/endpoints.py:21-113`](../../jvagent/logging/endpoints.py)) | [`/docs/logging.md`](../../docs/logging.md) |
| Action lifecycle errors | auto-logged by `enable()`/`disable()`/`reload()` wrappers ([`action/base.py:694-752`](../../jvagent/action/base.py)) | [`/docs/error-logging.md`](../../docs/error-logging.md) |
| Profiling (request latency) | `core/profiling.py` decorators | (no dedicated doc — see source) |
| Per-action telemetry | up to each action; typically merges into `Interaction.observability_metrics` | per-action source |
| Channel adapter delivery logs | per channel adapter | per-channel action source |

---

## 2. Architecture in one diagram

```
                   stderr / file
                        ▲
                        │
[Module loggers] ── configure_standard_logging ──► standard handlers
                        │
                        │ DBLogHandler (custom)
                        ▼
                   ┌──────────────────┐
                   │  jvspatial logs  │  separate DB (database_name="logs")
                   │       DB         │
                   └──────────────────┘
                        ▲
                        │
[INTERACTION events] ───┘   (registered level, emitted during walker traversal)


On each Interaction node (main DB):
  • observability_metrics: dict   (model calls, embeddings, latencies, errors)
  • usage:                dict   (tokens, model_call counts)
  • actions:              list   (which actions ran)
  • events:               list   (structured per-turn events)
  • directives:           list   (accumulated directives)
```

---

## 3. Tiers of granularity (least → most)

1. **Process logs** (`logging.getLogger`) — global, line-oriented, stderr / file.
2. **Bootstrap counters** (`StartupLogCounter`) — aggregated warning/error counts at boot.
3. **INTERACTION log records** — written to the `logs` DB per turn (filterable by agent_id + time).
4. **Per-interaction metric dicts** — `observability_metrics`, `usage` on the `Interaction` node. Survives until the Interaction is pruned.
5. **Action-level errors** — auto-logged with structured details (`agent_id`, `action_class`, `action_id`, `action_label`, `context`, `error_code`).

---

## 4. Standard reconfiguration

`cli/main.py:31-35` configures logging at process start:

```python
configure_standard_logging(
    level=env("JVSPATIAL_LOG_LEVEL", default="INFO"),
    enable_colors=True,
    preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"],
)
```

To raise verbosity for a single run:
```bash
JVSPATIAL_LOG_LEVEL=DEBUG jvagent /path/to/app --debug
```

`--debug` ALSO drops the root logger + every handler to DEBUG and forces `jvagent` namespace to DEBUG ([`cli/main.py:154-160`](../../jvagent/cli/main.py)).

---

## 5. Querying the logs DB

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/logs/agents/$AGENT_ID?limit=100&since=2026-05-17T00:00:00Z"
```

Endpoint: `GET /logs/agents/{agent_id}`. Filters: time range, level, query (Mongo-style). Source: [`jvagent/logging/endpoints.py:21-113`](../../jvagent/logging/endpoints.py).

Full detail: [`/docs/logging.md`](../../docs/logging.md).

---

## 6. What's NOT instrumented (today)

- OpenTelemetry / distributed tracing. There are no spans across the walker.
- Prometheus / metrics export. Counters live on per-`Interaction` dicts; aggregation is by query.
- Request-level structured logs (no JSON log format by default; jvspatial's `configure_standard_logging` is text-mode).

If you add one of these, document it here and in [`/docs/logging.md`](../../docs/logging.md).

---

## 7. Retention

| Tier | Retention knob |
|---|---|
| `logs` DB records | `App.log_retention_days` (default `60`, [`core/app.py:65`](../../jvagent/core/app.py)) |
| Per-Interaction metrics | bounded by `Conversation.interaction_limit` (pruned with the interaction) |
| stderr / file logs | external — depends on your hosting |

---

## 8. Linked detail docs (already in repo, kept as-is)

- [`/docs/logging.md`](../../docs/logging.md) (734 lines) — comprehensive logging architecture
- [`/docs/error-logging.md`](../../docs/error-logging.md) (401 lines) — error rollup mechanics
- [`/docs/interaction-logging.md`](../../docs/interaction-logging.md) (275 lines) — turn-level events + INTERACTION level
- Local subsystem guide: [`/jvagent/logging/CLAUDE.md`](../../jvagent/logging/CLAUDE.md)
