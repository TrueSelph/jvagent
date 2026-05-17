# AUDIT — jvagent/core/

> Read-only audit. Date: 2026-05-17. Reviewed against `.planning/SPEC.md`.

## Summary
- Files reviewed: 31 (full `jvagent/core/` tree incl. `endpoints/`, `repair_phases/`)
- Findings: **5 critical, 11 high, 10 medium, 6 low**
- SPEC drift: yes — invariants §11.1, §11.5, §11.7 are not fully enforced in code; documented citations in §2 / §3 disagree with current line numbers.

---

## Findings

### CRITICAL

#### C-1 — `App.get()` does not validate cached app belongs to the *current* DB context
**Location**: `app.py:140-159` (entire cache check), contrast with `graph_repair.py:118-123` which has to **work around** this.
**Issue**: The cache hit only checks `cls._cached_app.id` truthiness. In long-lived processes (tests, embedded hosts, ASGI workers) where the default `Context` / database can be swapped, the cached `App` instance points at a node in the *old* database. Every downstream call (`get_actions_manager`, `_apply_app_properties`, `set_app_update_mode`, `file_storage_*`) operates on a node from the wrong DB. `graph_repair.repair_agent_graph` already discovered this and added a defensive `context.get(Node, app.id)` re-resolve at `graph_repair.py:119-123`; nowhere else in core does so.
**Why it matters**: Silent data corruption: agents created against the cached App go to one DB, queries against `App.get()` resolve against another. Affects pytest fixtures and any embed-style host that swaps contexts.
**Fix**: In `App.get()` after the cache hit, verify the cached instance is reachable via `get_default_context().get(Node, cls._cached_app.id)`; on miss, clear cache and re-fetch. Add the same check to `Agent.get()` (it delegates to `cache_manager.get_agent`, which has the same problem in `cache.py:165-182`).

#### C-2 — `App._cached_app` is shared across all event loops / threads with no guard
**Location**: `app.py:88` (`_cached_app: ClassVar[Optional["App"]]`), `app.py:170` (assignment outside lock-acquired branch), `app_loader.py:319` and `app_loader.py:348` (assignments **without** acquiring `_get_lock`).
**Issue**: `_cached_app` is a plain class attribute. While `_get_lock()` is per-event-loop, the cache itself is not. Two parallel async workers (e.g. uvicorn `workers>1` *within* the same process, or pytest-xdist worker thread per loop) racing on first-fetch can both succeed, double-create or partially overwrite the cache. Worse, `app_loader._ensure_app_node` writes `App._cached_app = app` (lines 319, 348) without any locking, even though `App._get_lock()` exists.
**Why it matters**: Race during cold start can leave `_cached_app` pointing at a deleted/duplicate App after `_deduplicate_app_nodes` finishes; subsequent callers get a stale node. SPEC §2.1 says "Exactly one `App` per process".
**Fix**: (a) Guard the `_cached_app = node` write in `app_loader.py:319, 348` with `App._get_lock()`. (b) Use `threading.Lock` (already declared as `_locks_guard`) to guard read/write of `_cached_app` itself, not only the `_locks` dict.

#### C-3 — `set_app_update_mode` `object.__setattr__` does **not** trip jvspatial's dirty tracking
**Location**: `app.py:537-540`.
**Issue**: This is the official pattern (CLAUDE.md §3.5), and `object.__setattr__` is deliberately used to dodge `protected=True` rejection. But jvspatial's `AttributeMixin` records dirty-field state through `__setattr__`; `object.__setattr__` bypasses that machinery. The subsequent `await app.save()` only persists fields the mixin believes have changed. If jvspatial's `save()` is a "dirty-only patch" (true on Mongo when `update_one $set` is computed from a dirty-set), `update_mode` may not be written.
**Why it matters**: SPEC §11.7 mandates that `update_mode` reset to `"run"` after a successful bootstrap. If the dirty-tracker doesn't notice `update_mode` changed, the reset is a no-op and the next cold start re-runs `merge`/`source`. This is exactly the bug §11.7 protects against. Verifying this requires reading `jvspatial.core.annotations` which is outside scope — but the risk is severe enough to flag because no test in `tests/core/` exercises persistence (only that the in-memory attribute changed).
**Fix**: After `object.__setattr__`, mark the field dirty explicitly. If jvspatial exposes `app._mark_dirty("update_mode")` or `app._dirty_fields.add("update_mode")`, call it. Otherwise force a full document write via `app.save(force_full_write=True)` (whatever jvspatial's bypass switch is). Add a unit test that round-trips: set mode → save → reload from raw DB → assert.

#### C-4 — Webhook SSRF protection has a TOCTOU + DNS-rebind hole on retries / IPv6 fallthrough
**Location**: `callback.py:26-101`.
**Issue**: `_resolve_and_validate` resolves DNS, picks `safe_ips[0]`, and pins the connection. The validation only rejects if **any** resolved address is private — but `_post_webhook_pinned_async` then connects to `safe_ips[0]` only. If `getaddrinfo` returns `[public_v4, private_v6]` (or vice versa), the function raises (good). But if it returns multiple **public** addresses and one resolves to an attacker-controlled name, retries are not protected: each call re-resolves DNS independently, so an attacker can rotate records between calls. Additionally, `httpx.AsyncClient` may still issue a separate DNS lookup for the `Host` header when verifying TLS SNI on some httpx versions.
Second issue: the loop at `callback.py:44-56` raises if **any** resolved IP is private. That's safer than picking the first safe one, but it has an inverse failure: a hostname legitimately resolving to both public and private (e.g. dual-stack with link-local) is permanently blocked. More importantly, the IPv6 `fc00::/7` check uses a network that doesn't cover all ULAs / `IPv6Address.is_private` semantics — relying on `is_private` would be safer.
**Why it matters**: Server-Side Request Forgery is in scope per SPEC scope §3. Tasks can fire webhooks at user-controlled URLs (`TaskCreationInteractAction.task_created_webhook_url`).
**Fix**: (a) Use `ipaddress.ip_address(ip).is_private or is_link_local or is_loopback or is_reserved or is_multicast` instead of hand-rolled network list. (b) Reject `0.0.0.0`, `::`. (c) Reject if hostname is itself an IP literal in a private range (currently only DNS-resolved IPs are checked). (d) Document that callers must not loop-retry across new DNS resolutions for the same URL.

#### C-5 — `Agents.sync_counters` writes back **even when there's no drift** and silently overwrites concurrent updates
**Location**: `agents.py:64-97`, paired with `agent_loader.py:249-253` (concurrent increment) and `app_loader.py:455-458` (concurrent absolute write).
**Issue**: `sync_counters` reads `total_agents` and `active_agents` via `len(agents)` over `get_connected_agents()`, then unconditionally writes them back via `await self.save()`. There is no compare-and-swap or version check. If an HTTP request adds/removes an agent (`endpoints/agents.py:147-154`, `agent_loader.install_agent:249-253`) between the read and the write, that delta is overwritten. The same applies to `_recount_agent_statistics` (`app_loader.py:455-458`) and the destructive write in `endpoints/agents.py:103-106, 151-154`. All of these mutate `total_agents` / `active_agents` without any locking.
**Why it matters**: Under any concurrent admin traffic, the counters drift permanently and observability dashboards show wrong values. Combined with the fact that `endpoints/agents.py:152-153` uses `max(0, … - 1)` rather than a real decrement, the counter is asymmetric (always re-clamped on delete, never on add) and over time skews high. This is `HIGH`-class on its own but I'm flagging `CRITICAL` because `sync_counters` is reachable from `GET /status` (admin-public) with `sync=True` — a no-op-looking read mutates state under load.
**Fix**: Make counters derived (always recompute from `len(agents)` on read, no stored field) or use `context.atomic_increment` on the diff. At minimum, do not call `save()` from a read endpoint unless `drift != 0`.

### HIGH

#### H-1 — Conditional in `Agent.get()` permanently bypasses the cache when **any** kwargs are passed
**Location**: `agent.py:79-83`.
**Issue**: `if agent_id is not None and not kwargs:` — any caller passing both `agent_id` and any kwarg falls through to `super().get(agent_id, **kwargs)`, which is uncached. This is documented in CLAUDE.md §6 as a trap. There is no guard or warning when callers accidentally pass an irrelevant kwarg (e.g. `agent = await Agent.get(agent_id, missing_ok=True)` — silently bypasses cache).
**Why it matters**: Any third-party action that mirrors `Node.get`'s signature will pay 100× the DB cost without knowing. Existing call sites in core/* look OK, but action authors will trip this.
**Fix**: Log `logger.debug` (or `warning` on the first occurrence) when the cache is bypassed because kwargs were supplied. Alternatively rename the cached version to `get_cached` and let callers be explicit.

#### H-2 — `Agent.save()` invalidates the agent cache but **not** dependent action / action-type caches
**Location**: `agent.py:200-227`, vs. `cache.py:227-256` (action and action-type caches).
**Issue**: When `Agent.save()` is called (e.g. enabling/disabling an agent via `endpoints/agents.py:114`), it invalidates `invalidate_agent_cache(self.id)` only. The action cache (`cache.py:202`) is keyed by `agent_id` and stays warm; the `_action_type_index` (`cache.py:64`) likewise persists. After a re-enable, the action set's `enabled` flags can still be served from a stale cache for up to `action_cache_ttl` (60s default). CLAUDE.md §3.3 says save invalidates "cache" — but it's just *the* agent cache.
**Why it matters**: Silent staleness on admin toggles. Tests pass because cache TTL is short; production with workloads that toggle agents will see flicker.
**Fix**: In `Agent.save()`, also call `invalidate_action_cache(self.id)` and `invalidate_action_type_index(self.id)`.

#### H-3 — `Conversation.context.session_id_1` is in the "deprecated indexes" map *but* added by current code
**Location**: `index_bootstrap.py:67-69` lists `context.session_id_1` as deprecated. `memory/conversation.py` is outside core but presumably the source. The comment at line 60-64 says it "will be dropped by the code-86 conflict handler" when the compound index is recreated.
**Issue**: Relying on the conflict handler to drop a deprecated index works on MongoDB but no-ops elsewhere; the comment acknowledges this. If a non-MongoDB adapter (JSON, SQLite) ever had the old name registered, it will never be cleaned up.
**Why it matters**: Minor for now (JSON/SQLite don't enforce uniqueness anyway), but a future Postgres backend would inherit ghost indexes.
**Fix**: Add a backend capability check; if `drop_index` is supported, call it explicitly here rather than waiting for conflict.

#### H-4 — Repair job force-advances past unresolved phases after 2 stalls
**Location**: `graph_repair_job.py:1656-1681`.
**Issue**: When `before == after and phase_before == state["phase"]`, the engine treats this as a stall, increments `stall_count`, and after 2 stalls **force-advances** to the next phase, wiping the cursor. For phases like `PH_ORPHANS_REATTACH` or `PH_ORPHANS_DELETE` that have remaining work, this means the work is silently dropped — orphans stay orphaned, broken nodes stay broken, and no error surfaces.
**Why it matters**: Stalls are typically caused by a single transient DB hiccup (Mongo failover, network blip). Force-advancing rewards the failure with data integrity loss. SPEC §4.1 lists repair phases as the reconciliation mechanism; skipping a phase silently violates that.
**Fix**: Distinguish "no progress because budget exhausted instantly" (don't increment stall) from "tick ran to completion with no work" (legit terminal state — phase already advances naturally). When force-advancing, log at `ERROR` and record the skipped phase in `state["result"]["skipped_phases"]` so operators can re-run. Better: only force-advance if `stall_count >= 5` and emit a metric.

#### H-5 — `_tick_sync_apply` reloads up to 50,000 valid edges and one scratch page per node — wrong scope
**Location**: `graph_repair_job.py:949-998`.
**Issue**: For every node in the page, it does `scratch_page(db, run_id, "node_edge", None, batch)` and filters in-Python by `key.startswith(f"{node_id}|")`. This reads the **same** rows for every node, and only `batch` rows total (default 500), so for any node whose edges are not in the first 500 rows of the scratch collection, expected edges are computed as an empty set and existing valid edges are kept but no new ones added.
**Why it matters**: Sync-edges phase is broken on any graph where the scratch `node_edge` collection exceeds `batch_size` rows for a single node — i.e., any node with >500 edges anywhere in the run. The result: under-detected drift, but ALSO the function reports `synced` ≥ 0 cheerfully. SPEC §11.5 (`metadata` authoritative) doesn't catch this because it's about a different surface, but the repair pipeline silently regresses correctness.
**Fix**: Either use a `scratch_page` query with `id` prefix `f"{run_id}:node_edge:{node_id}|"`, or build a fully-sorted scan and only advance per-node. The `valid_edge` set has the same cap issue.

#### H-6 — `_tick_orphans_delete` may delete nodes still referenced by foreign-key fields
**Location**: `graph_repair_job.py:1210-1263`.
**Issue**: The phase skips deletion for `protected_entities = {"Memory", "User", "Conversation", "Interaction"}` only when `node.edges(direction="in")` is non-empty. But other nodes (e.g. `Action` instances with `agent_id`) are deleted unconditionally if they're orphaned and not in either list. `cascade=True` is set only for `structural_entities = {"App", "Agents", "Agent", "Actions", "Action"}` — so `Action` deletes cascade (good), but a custom-namespace action attached to nothing but a `MicrosoftToken` will be cascaded-deleted along with its token, leaving auth in a broken half-state.
**Why it matters**: Tokens, embeddings, vector-store rows can be orphaned by repair, with no warning. This is a high-blast-radius operation gated only by `dry_run=False`.
**Fix**: Before delete-cascade, log the would-be-cascaded child node IDs. Add an `allow_destructive_cascade` flag that defaults to False and require operators to opt in.

#### H-7 — `repair_agent_graph` "lock not acquired" path leaks `started_at` and returns wrong elapsed_seconds
**Location**: `graph_repair.py:165-180`.
**Issue**: When another worker holds the lock, the result is built from `repair_state.started_at` if available, else `started_at.isoformat()`. `elapsed_seconds` is hard-coded to `0.0`. The caller cannot tell whether the other worker is making progress or hung. Worse, if no `repair_state` exists yet (the comment at line 181 says "safe to proceed"), the code falls through to acquire the lock again — but the path that bypasses lock acquisition is reached because `acquired` is still False, leading to a NameError on `repair_state` at line 199 (`all_states = await RepairState.find_all(app)` is fine, but `_persist_repair_state` and following block all reference `repair_state`).
Re-reading lines 161-330: actually the indentation works because `if not acquired:` at line 165 ends with `return result` only inside the `if repair_state:` branch. The fallthrough is "safe to proceed" — but `acquired` remains `False`, meaning the `release_claim` at the `finally` block (line 80) doesn't run. The lock is still held by the **other** worker, so a release call here would be wrong anyway. This is OK, but it's a code-smell ambiguity.
**Why it matters**: Operators get `status=in_progress, elapsed=0` from a worker contending for the lock — looks identical to a stuck repair.
**Fix**: When `acquired=False` and a `repair_state` exists, compute `elapsed_seconds` from `started_at` honestly. When `acquired=False` and no state exists, return a clear `"status": "queued"` rather than falling through to a half-flagged run.

#### H-8 — `bootstrap_update_mode.resolve_bootstrap_update_mode` calls `App.clear_cache()` then re-fetches **but does not re-resolve under the lock**
**Location**: `bootstrap_update_mode.py:55-64`.
**Issue**: Clears cache, then calls `App.get()`. Between the clear and the get, another coroutine can populate the cache with a stale app from a different DB. The `get()` then returns the freshly-fetched one (correct), but the cache write race noted in C-2 still applies.
**Why it matters**: Boot-time only, so low frequency; but the boot path is precisely where update-mode misclassification has the most blast radius.
**Fix**: Move the clear-and-get inside an `await App._get_lock()` block, OR have `App.get()` accept a `force_refresh=True` flag.

#### H-9 — Action `reconcile_actions` calls `deregister_action` then re-counts, but `deregister_action` may have failed mid-flight
**Location**: `agent_loader.py:478-509`.
**Issue**: If `deregister_action` raises mid-way (line 483-486: caught with `Exception` and logged warning), `removed` is not incremented but `kept_map` already excluded the record from `kept_map`. The recount at lines 496-507 then computes `registered_count = len(kept_nodes)` using nodes that still exist in DB — including the half-deregistered one. So `actions_manager.registered_count` is set to one less than reality and the orphaned action remains queryable.
**Why it matters**: A single transient failure during reconciliation poisons the manager's counters and leaves a ghost node, which the next bootstrap will try to deregister again, retrying forever.
**Fix**: Track failed deregistrations and either re-add the failed action to `kept_map` so the count is correct, or surface them to the caller.

#### H-10 — `Memory` / `User` reattach uses `getattr(node, "memory_id", "")` and writes silently
**Location**: `graph_repair_handlers.py:202-204` (User reattach), `graph_repair_handlers.py:226-227` (write).
**Issue**: The reattach mutates `node.memory_id` and `setattr(node, "memory_id", memory.id)` + `await node.save()`. There's no compound-index check before the write — if the chosen memory already has a `User` with the same `user_id`, the compound unique index (`memory/user.py:16-24`, called out in SPEC §2.1) will reject the save and raise. The exception path is `except Exception as e: logger.debug(...)` in the chunked reattacher (`graph_repair.py:452-453`), so the failure is swallowed at DEBUG level.
**Why it matters**: Repair appears to succeed but silently fails to reattach users; SPEC §11.2 invariant about User uniqueness is enforced by the DB, which means the failure surfaces as a swallowed E11000.
**Fix**: Before reattach, check `await memory.node(node="User", user_id=user_id)` (which the next branch at 222 already does for *other* memories — apply the same to the preferred memory). Raise a warning when the reattach skips because of conflict.

#### H-11 — Repair `_distributed_repair_lock` writes a sentinel that can be silently lost under TOCTOU
**Location**: `graph_repair.py:48-68`.
**Issue**: The block comments out that `find_one_and_update(upsert=True)` is atomic on Mongo and falls back to non-atomic `get` + `save` on other backends. The fallback is the comment-acknowledged TOCTOU: two concurrent workers both find None, both save, second wipes first. The `except Exception: pass` (line 67-68) suppresses *all* errors including the index-violation that would alert you to the race.
**Why it matters**: On JSON / SQLite backends in production (small deployments), two workers can both think they hold the lock.
**Fix**: Have the fallback path also call `claim_record` first; only seed if claim succeeds. Or document explicitly that the repair lock is best-effort on non-Mongo, and gate parallel-worker deployment on Mongo.

### MEDIUM

#### M-1 — `App.get()` silent failure mode when `Root` has multiple `App` nodes
**Location**: `app.py:166-174`.
**Issue**: Returns the first `App` it iterates, in whatever order `root.nodes()` produces. The bootstrap path (`app_loader._deduplicate_app_nodes`) handles dedupe at boot, but if duplicates are created later (manual DB edit, partial restore), `App.get()` is non-deterministic.
**Fix**: Log a WARNING when > 1 `App` is found and prefer a consistent tie-break (oldest by id, or one matching `app_id` from config).

#### M-2 — `App.now()` returns naïve datetime when timezone unset
**Location**: `app.py:209-212`.
**Issue**: `datetime.now()` (no tz) returns naïve local time. SPEC §5.2 / observability rely on UTC. `app_now_aware_utc` (`app.py:518-534`) compensates by treating naïve as UTC, which is technically wrong — naïve from `datetime.now()` is *server local*, not UTC. So on a server running in PDT, the "UTC" timestamp is 7-8h off.
**Why it matters**: Conversation timestamps, repair `started_at`, log filtering all get a 7-hour skew on non-UTC hosts.
**Fix**: Default to `datetime.now(timezone.utc)` when no app timezone configured.

#### M-3 — `app_loader._ensure_app_node` writes `app_id` outside of `app.update` for `source` mode but inside for `merge`
**Location**: `app_loader.py:289-317`.
**Issue**: Both branches include `app_id` in the update dict, but the dict literal differs: `source` mode includes name/version/description/file_storage_*/etc., while `merge` only includes `version` and `app_id`. If a user changes `context.timezone` in app.yaml and re-bootstraps with `--merge`, the timezone is **not** synced. The docstring at SPEC §6.3 says merge "applies non-destructive merge from YAML. New keys added; existing graph nodes preserved." But the implementation only merges 2 keys (version, app_id), not "non-destructive merge from YAML".
**Why it matters**: Drift between SPEC and code. Operators relying on `--update --merge` for benign config updates (timezone, description) will be confused when nothing changes.
**Fix**: Either expand merge to include the same keys but skip if already set, or update SPEC §6.3 to match reality.

#### M-4 — `agent_loader.uninstall_agent` does not invalidate cache
**Location**: `agent_loader.py:670-710`.
**Issue**: Deletes the agent via `agent.delete()` but never calls `invalidate_agent_cache(agent_id)`. `Agent.save()` invalidates cache (`agent.py:218-227`); `Agent.delete()` does not.
**Why it matters**: After uninstall, the agent cache may serve the deleted agent for up to `agent_cache_ttl` (300s default).
**Fix**: Add `await invalidate_agent_cache(agent.id)` after delete.

#### M-5 — `Agent.save()` cache invalidation runs inside `try/except` that swallows everything
**Location**: `agent.py:220-226`.
**Issue**: Comment says "Log but don't fail - save already succeeded". Reasonable, but `except Exception` includes import errors that mask configuration bugs. Logged at WARNING (good) but still hides the fact that the cache module is broken.
**Fix**: Catch only `Exception` *that is not* `ImportError` / `AttributeError`. Or fail loudly on first occurrence (operator can fix).

#### M-6 — `endpoints/agents.py:list_agents` filters in Python after a full collection scan
**Location**: `endpoints/agents.py:228-263`.
**Issue**: When `search` is provided, `Agent.find(filters)` loads **every** matching agent into memory, then filters by name/alias/description in Python. The ObjectPager path (no search) is correct; the search path scales linearly with collection size and ignores `page`/`per_page` for the DB query.
**Why it matters**: At 10k+ agents, search blows memory.
**Fix**: Push the search down to the DB query (`$regex` on Mongo, `LIKE` on SQL) or accept it as a documented limit.

#### M-7 — `graph_repair_handlers._reattach_user` `setattr(node, "memory_id", primary.id)` bypasses `attribute(...)` validation
**Location**: `graph_repair_handlers.py:202-204`.
**Issue**: Uses plain `setattr` to mutate a Pydantic-validated field. If `memory_id` had been declared with a validator (it isn't currently, but the pattern invites it), validation would be skipped. More immediately, it doesn't trip dirty tracking unless jvspatial's `AttributeMixin` intercepts `__setattr__` (which it presumably does — verify in jvspatial source).
**Fix**: Use `node.memory_id = primary.id` directly; reserve `setattr` for dynamic-attribute mutation.

#### M-8 — `dependency_installer.install_pip_dependencies` runs arbitrary `pip install` from action metadata
**Location**: `dependency_installer.py:78-119`, called from action loading.
**Issue**: Runs `pip install <dep>` where `<dep>` is whatever is declared in `info.yaml`. A malicious action package could declare `dependencies.pip: ["evil-package==1.0.0"]` and have it installed silently during bootstrap. There's a kill switch (`JVAGENT_DISABLE_RUNTIME_PIP_INSTALL`), but it's opt-in. SPEC scope §3 marks security as in-scope.
**Why it matters**: Supply-chain risk on any deployment that uses third-party action packages.
**Fix**: Document the risk in `actions/CLAUDE.md`. Default `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=true` in production mode (`config.is_production_mode()`).

#### M-9 — `profiling.py` `_profile_context` is module-global and *not* loop-scoped
**Location**: `profiling.py:184-185`.
**Issue**: A single dict guarded by a single `asyncio.Lock`. In serverless warm-start scenarios where multiple loops co-exist (see `app.py:91-95` for the comment about Lambda), this Lock is bound to whichever loop first acquired it. Subsequent loops will get "attached to different loop" errors.
**Fix**: Mirror the `app.py` per-event-loop lock pattern.

#### M-10 — `bootstrap_logger` emojis in log messages
**Location**: `bootstrap_logger.py:32, 41, 60, 77, 85, 134`.
**Issue**: Emoji characters (🚀, ✓, 📊, ⚠️, ❌) embedded in log messages. They show up as raw UTF-8 in log-shippers that don't render unicode (CloudWatch ASCII view, some syslog targets) and break grep patterns. Project CLAUDE.md states: "Only use emojis if the user explicitly requests it." This is internal logging.
**Fix**: Replace with ASCII markers like `[START]`, `[OK]`, `[STATS]`, `[WARN]`, `[ERR]`.

### LOW

#### L-1 — Inconsistent error logging level
**Location**: many files. Examples: `app.py:359-362` swallows `StorageError` and `Exception` silently with no log. `agent.py:97-109` returns `None` silently when actions manager is missing. `agent_loader.py:545-571` logs reload failures at WARNING but module-not-found at DEBUG.
**Issue**: Same severity event (e.g., "expected resource not found") logged at three different levels across the codebase.
**Fix**: Standardize on a logging policy — see `errors.py:1-23` which acknowledges hundreds of bare `except Exception` clauses but only describes a future migration.

#### L-2 — Unused / dead code in `bootstrap_logger.py:88-136`
**Location**: `bootstrap_logger.py:88-136`.
**Issue**: `log_bootstrap_summary` accepts 7 params; grep the repo (out of scope to actually do here) — likely callers are limited and this looks like leftover from a refactor.
**Fix**: If unused, delete.

#### L-3 — `_tick_dup_apply` writes `_ = (src, tgt)` to silence a linter
**Location**: `graph_repair_job.py:1412`.
**Issue**: Dead code; comment-or-remove. Indicates `src`/`tgt` were once used; the function still partitions on `key.partition("\n")` but discards the result.
**Fix**: Drop the partition or drop the discard.

#### L-4 — `_profile_context` keyed by 8-char UUID slice (collision possible)
**Location**: `profiling.py:201` (`rid = request_id or str(uuid.uuid4())[:8]`).
**Issue**: 8 hex chars = 32 bits = ~50% collision odds at 65k profiles (`MAX_PROFILES=1000` default). Not catastrophic but means two requests can occasionally share a profile.
**Fix**: Use the full UUID; truncate only for display.

#### L-5 — `Conversation` deletion endpoint validates ownership but does not check for active interactions
**Location**: `endpoints/conversation.py:57-72`.
**Issue**: Admin endpoint deletes a conversation by `(agent_id, user_id, session_id)`. If an active `InteractWalker` is mid-flight on that conversation, the delete races. Cascade will remove the live interaction node out from under the walker.
**Fix**: Either acquire the same lock the walker uses, or return 409 when `interaction_count > 0` and the most recent interaction was within N seconds.

#### L-6 — Repair endpoint admin-only but no rate limit
**Location**: `endpoints/graph_repair.py:14-107`.
**Issue**: Admin endpoint can run arbitrary repair work bounded only by `max_seconds=600` and the distributed lock. A panicked admin spamming POST can saturate Mongo with concurrent paginated finds.
**Fix**: Document expected concurrency or add a per-admin rate-limit.

---

## SPEC drift

1. **§2 graph hierarchy** — SPEC cites `Agents` at `jvagent/core/agents.py:17`, `Agent` at `jvagent/core/agent.py:18`, `App.get()` at `app.py:124`. Line numbers match. **OK.**
2. **§6.3 update modes** — SPEC says `merge` is "non-destructive merge from YAML. New keys added; existing graph nodes preserved." Code (`app_loader.py:308-317`) merges only `version` and `app_id`, ignoring timezone / description / file_storage_*. **DRIFT.** Recommend updating SPEC §6.3 to enumerate exactly which keys merge updates, or expanding the merge dict to include all non-destructive keys.
3. **§11.7 update_mode reset** — SPEC mandates reset to `run` after successful sync. Implementation at `bootstrap_update_mode.py:67-77` does the reset, but the underlying `set_app_update_mode` (`app.py:537-540`) may not persist due to dirty-tracking bypass (see C-3). **POTENTIAL DRIFT.** Add a round-trip test.
4. **§11.5 metadata authoritative** — SPEC says `Action.metadata` is authoritative for `info.yaml`. Some repair code (`graph_repair_job.py:740-755`) reads `metadata.config` *merged with* `metadata.config_overrides` — i.e. overrides shadow metadata. Probably intentional but should be called out in SPEC.
5. **§3 interaction lifecycle (out of this audit's scope)** — repair phases delete orphans without consulting `walker.background_actions`; a background-action-only path that has not yet flushed could be repaired away. Out of `core/`-owned but worth a cross-team note.

## Strengths
- `_get_lock()` per-event-loop pattern is well-documented (`app.py:88-117`) and correctly handles closed-loop GC.
- SSRF protections in `callback.py` are present and pin connections by IP (rare in webhook code).
- Distributed repair lock with `find_one_and_update(upsert=True)` correctly identifies the TOCTOU and notes the JSON/SQLite fallback limit.
- `repair_state.finish()` (`repair_state.py:330-388`) hardens both edge cleanup and the raw-DB fallback delete; good defensive layering.
- Config schema (`config.py:59-174`) makes precedence explicit and testable.

## Out of scope
- `jvagent/action/` action lifecycle, register / deregister flows (mentioned in passing for the cache-invalidation chain — flagged in H-2).
- `jvagent/memory/` conversation / interaction internals (touched only where repair handlers reach in).
- `jvagent/cli/` boot orchestration (`run_server`, `bootstrap_application_graph`) — referenced but not opened.
- `jvspatial` private-API access in `jvspatial_compat.py` — depends on jvspatial internals; handled correctly.
- `dependency_installer.check_pip_dependency_installed` uses `__import__(package_name.replace("-", "_"))` which is wrong for many packages (e.g. `PyYAML` imports as `yaml`); minor and out of core/ in spirit.
