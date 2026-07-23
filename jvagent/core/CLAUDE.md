# jvagent/core/ — Agent Guide

> Local guide for editing the core subsystem. Cross-link: root [`/CLAUDE.md`](../../CLAUDE.md), spec [`/.planning/SPEC.md`](../../.planning/SPEC.md).

---

## 1. What this directory owns

The graph-level skeleton of an app:

- `App` (singleton root node) and `Agent` / `Agents` nodes.
- YAML loaders that translate `app.yaml` and `agent.yaml` into graph state.
- Config resolution (env → app.yaml → defaults).
- Bootstrap / update-mode handling (`run` / `merge` / `source`).
- Graph repair (stale node cleanup, reconciliation).
- Caching, profiling, observability primitives.
- Core HTTP endpoints (under `core/endpoints/`).

It does **not** own: per-user state (that's `memory/`), action plugins (that's `action/`), HTTP server bootstrap (that's `cli/`).

---

## 2. Key files

| File | Purpose |
|---|---|
| `app.py:21` | `App` node — singleton, file storage, timezone, update_mode |
| `app.py:90-124` | Per-event-loop lock pattern (use this verbatim for any node singleton) |
| `app.py:596` | `set_app_update_mode` — example of mutating a `protected=True` field |
| `agent.py:30` | `Agent` node + `Agent.get(agent_id)` cached fetch |
| `agent.py:108-118` | Cache-aware fetch via `cache_manager.get_agent()` |
| `agent.py:244` | `Agent.get_memory()` — resolves attached `Memory` node |
| `agent.py:256` | `Agent.get_response_bus()` — lazy per-agent `ResponseBus` singleton |
| `agent.py:271-358` | `Agent.send_proactive_message()` — programmatic, response-only message send; resolves User/Conversation, creates empty-utterance Interaction, publishes via `ResponseBus` (auto-records + auto-dispatches to channel adapter). See [`docs/proactive-messages.md`](../../docs/proactive-messages.md). |
| `agents.py:17` | `Agents` branchpoint node |
| `app_loader.py` | `app.yaml → App` translation |
| `agent_loader.py` | `agent.yaml → Agent + Action graph` |
| `app_yaml_validator.py` | Schema-level validation of `app.yaml` |
| `agent_yaml_validator.py` | Same, for `agent.yaml` |
| `config.py:60-150` | `ConfigKey` / `ConfigSchema` precedence resolution |
| `env_resolver.py` | Expands `${ENV_VAR}` placeholders in config |
| `cache.py` | Per-agent action-type index; `cache_manager` for `Agent.get()` |
| `bootstrap_logger.py` | Startup logging context manager |
| `bootstrap_update_mode.py` | `--update` / `--merge` / `--source` handling |
| `graph_repair*.py` | Reconciliation jobs (stale node cleanup, orphan repair) |
| `graph_repair_job.py` | Top-level repair orchestration |
| `endpoints/` | Core HTTP routes (`agents.py`, `app.py`, `conversation.py`, `status.py`, `graph_repair.py`) |
| `app_context.py` | `set_app_root()` / `get_app_root()` — used by CLI on startup |
| `jvspatial_compat.py` | Version compatibility shims |

---

## 3. Contracts (don't break)

1. **`App` is a singleton.** Always go through `await App.get()` — never construct it directly.
2. **`App._cached_app` and per-loop lock** ([`app.py:90-124`](app.py)) must be preserved exactly. Serverless warm-starts depend on it.
3. **`Agent.save()` invalidates cache** ([`agent.py:408`](agent.py)). If you bypass `.save()`, also invalidate manually via `invalidate_agent_cache(agent.id)`.
4. **`App.update_mode` resets to `run`** after a successful bootstrap (in `cli/server.py:run_server` / `bootstrap_only`). Don't let one-shot merge/source operations persist.
5. **`protected=True` fields require `object.__setattr__` + `await save()`** (see [`set_app_update_mode`](app.py)). Plain assignment is dropped silently in bulk overwrite paths.
6. **App.now() timezone semantics** ([`app.py:251`](app.py)):
   - Returns `datetime` if `fmt` is `None`, else a formatted `str`.
   - If `App.timezone` (IANA name, e.g. `America/New_York`) is set, the
     returned `datetime` is **timezone-aware** in that zone.
   - If `App.timezone` is unset or invalid, returns `datetime.now()` —
     **naïve local time** (NOT UTC). Be careful with arithmetic against
     timezone-aware datetimes.
   - `app_now_aware_utc(app)` ([`app.py:577`](app.py)) normalizes both
     branches into a `datetime` aware in UTC. Use it whenever you need to
     compare or subtract `App.now()` against another timestamp.

---

## 4. Adding to this directory

| If you're adding... | Read first |
|---|---|
| A new App-level config key | `config.py` (`ConfigKey`, `ConfigSchema` pattern) |
| A new App field | Match the `attribute(...)` style from `app.py`. Add to `app_yaml_validator.py`. |
| A graph repair phase | `graph_repair_job.py` — phase tick functions are called from `run_repair_session`. |
| A core HTTP route | `endpoints/`. Use `@endpoint("/api/...")` with `auth=True, roles=["admin"]` unless public. |
| A bootstrap hook | `cli/bootstrap.py` is the orchestrator; add intermediate steps in `core/startup.py`. |
| Programmatic proactive send (agent → user, no inbound) | Call `await agent.send_proactive_message(user_id=..., content=..., channel=...)`. See [`docs/proactive-messages.md`](../../docs/proactive-messages.md). Do NOT publish to `ResponseBus` directly from outside the walker pipeline — use this method so the bound Interaction is created and the response is recorded. |

---

## 5. Tests

`tests/core/` mirrors this layout. Run a slice:

```bash
pytest tests/core/ -v
```

For bootstrap-flow regressions: `tests/test_stress_seed_graph.py` exercises a full graph build.

---

## 6. Traps specific to core/

| Trap | Fix |
|---|---|
| Reading `app.file_storage_provider` before `App` is loaded | `await App.get()` first; it may be `None` during cold init. |
| Calling `Agent.get()` with kwargs and an agent_id | The kwargs path bypasses cache. Pass only `agent_id` for cached lookups ([`agent.py:89`](agent.py)). |
| Forgetting to `await save()` after mutating a config key on the App node | Persists nothing. Add the `save()`. |
| Manually constructing event-loop locks | Use the dict-keyed-by-`id(loop)` pattern from [`app.py:90-124`](app.py). |
| Skipping `agent_yaml_validator` after editing `agent_loader.py` | YAML schema drift. Update both. |

---

## 7. Don't touch from outside core/

- `Agent` / `Agents` / `App` class definitions — they're the canonical Node shape.
- `cache.py` invalidation contract — if you change cache keys, find every call site of `invalidate_agent_cache`.
- `graph_repair_*.py` — repair phases run on every cold start; bugs here cause user-visible boot failures.

---

## 8. Out of scope here

- Per-user data (User/Conversation/Interaction): see `jvagent/memory/`.
- Action lifecycle (`on_register`, `on_enable`, etc.): see `jvagent/action/CLAUDE.md`.
- HTTP server start: see `jvagent/cli/CLAUDE.md`.
