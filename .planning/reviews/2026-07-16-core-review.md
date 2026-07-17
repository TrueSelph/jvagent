# jvagent Core Review — Findings & Improvement Plan

**Date:** 2026-07-16
**Scope:** Core (bootstrap, App/Agent/Actions graph), memory subsystem, orchestrator, interact pipeline + session tokens, bundled actions, CLI/logging/graph-repair.
**Method:** One inline core-bootstrap review + five parallel subsystem reviewers. Every finding verified against actual code paths (and jvspatial internals where relevant), not inferred from names.

---

## 0. The through-line

One structural fault explains the duplicate-singleton behavior you flagged **and** a large fraction of everything else:

> **jvagent relies on uniqueness/atomicity guarantees the runtime does not provide.**
> Unique indexes are declared but the default DB adapter (`json`) makes `create_index` a no-op. Mutual exclusion is declared via `asyncio.Lock` attributes that are per-deserialized-instance and per-process. The distributed lock that *does* exist has a lease shorter than a turn and no renewal. So every "check-then-create" and "read-modify-write" in bootstrap and memory is a live race on any real multi-worker/multi-replica deployment, and produces **persistent** corruption on the default adapter.

Fix the substrate (identity + locking) first; most of the high-severity items collapse once it holds.

---

## 1. Severity-ranked findings

### CRITICAL / HIGH — data integrity & isolation

| # | Area | Defect | Anchor |
|---|------|--------|--------|
| C1 | Bootstrap | **Duplicate action nodes.** `Action.find_one` filters by *imported* subclass names (`_build_database_query` → `__subclasses__()`); a persisted action whose class isn't imported at check time is invisible → duplicate saved. Reconciliation only runs under `--update`; a plain `jvagent` run never dedupes. | `action/actions.py:76,97`; `core/agent_loader.py:402` |
| C2 | Bootstrap | **No DB uniqueness backstop.** Unique compound index `agent_label` declared but default `json` adapter no-ops `create_index`. Every check-then-create race persists a real duplicate. | `action/base.py:39`; `cli/server_config.py:44` |
| C3 | Bootstrap | **Unique-index shape wrong.** `agent_label` key = `(agent_id,label)` without `namespace`; identity elsewhere is `(agent_id,namespace,label)`. Same label in two namespaces → E11000 on Mongo, action silently lost (`register_action` swallows all exceptions). Agent `name` unique index likewise omits `namespace`. | `action/base.py:39`; `core/agent.py:44` |
| C4 | Bootstrap | **Useless lock.** `Actions._lock` is `attribute(private, default_factory=asyncio.Lock)` — per-instance, per-process. No distributed bootstrap lease in `pre_startup_bootstrap`. Concurrent workers/replicas bootstrap simultaneously. | `action/actions.py:35`; `cli/server_config.py:569` |
| C5 | Memory | **Duplicate User** across workers — `Memory.get_user()` guarded only by in-process lock; unique index no-op. Two first-contact messages → two `(memory_id,user_id)` rows; writes/usage split. | `memory/manager.py:76-154`; `memory/user.py:16` |
| C6 | Memory | **Duplicate Conversation per session_id** — `get_session` creates the Conversation *before* the turn-lock (which is keyed on that conversation id). Concurrent first messages fork the conversation; the two copies get different `token_secret` → session tokens intermittently fail. | `memory/manager.py:441-524`; `interact_walker.py:505-522` |
| C7 | Memory/Orch | **Turn-lock lease < turn length, no renewal.** Redis/Dynamo lease default 45s, no heartbeat; orchestrator budget is 24 model round-trips / duration cap disabled. Lease lapses mid-turn → second worker runs concurrently → forked chain + lost TaskStore updates (whole-list rewrite). | `memory/distributed_conversation_lock.py:133-174,210-296`; `orchestrator_interact_action.py:334-338`; `task_store.py:718-753` |
| C8 | Memory | **Lock-holder contextvar leaks into background tasks.** `create_task` snapshots context; a `run_in_background=True` task inherits `_lock_holder=conversation_id` forever → later `add_interaction` (e.g. `send_proactive_message`) mutates the chain with **no lock**. | `memory/distributed_conversation_lock.py:26-33,89-93` |
| C9 | Memory | **Whole-document lost updates.** Conversation/User saves are read-modify-write of the full node; the turn instance, `enqueue_proactive_task` (no lock), and the embed path hold distinct in-memory copies. Last save wins → enqueued tasks vanish or counters roll back. | `task_store.py:718-753`; `core/agent.py:366-411`; `memory/user.py:250-293` |
| H10 | Actions | **Reply subscribe IDOR.** `reply_subscribe_endpoint` authenticates caller but never checks `session_id` ownership; `stream=false` pops the session queue → cross-user disclosure + theft of pending messages. | `action/reply/endpoints.py:147-198` |
| H11 | Actions | **Reply publish spoofing.** `reply_publish_endpoint` is `auth=True` but no `roles=["admin"]` and no ownership check → any authed user injects agent-attributed content into any session/agent. | `action/reply/endpoints.py:90-136` |
| H12 | Orch | **Directive contract trusts every tool result.** `next_tool`/`response_directive` parsed from *any* observation via plain `json.loads`; a directive is delivered as the reply bypassing the model, `next_tool` forces tool-chaining. MCP/third-party tools can speak the orchestrator's private control protocol → egress hijack. No allowlist. | `orchestrator_interact_action.py:1852-1870`; `loop.py:722-772,448-465` |
| H13 | Orch | **Two permanent turn-lock traps.** `locked_denied` (access revoked mid-flow) and `locked_silent` (IA runs without emitting/completing) reply a dead-end every turn forever; neither cancels the control-task nor counts the error-streak escape. | `loop.py:113-131`; `continuation.py:199-252` |
| H14 | Actions | **`get_model_action()` base fallback is dead code.** Falls back to `get_action(LanguageModelAction)` (exact-entity match); no node persists that entity → always None. Agent with only a non-OpenAI LM + default `model_action_type="OpenAILanguageModelAction"` silently loses identity compose. Every test mocks `get_model_action`, so untested. | `action/base.py:962-965`; `reply/reply_action.py:140` |
| H15 | Actions | **Streaming `_last_result` cross-request contamination.** Set on the shared cached action instance; `track_usage` runs in a `finally` after a concurrent request overwrote it → user B's prompts/response persisted into user A's `interaction.observability_metrics`. | `model/language/base.py:865`; `model/base.py:296-299` |
| H16 | Actions | **Code-execution "per-user isolation" is cwd-only.** Default `SubprocessExecutor` has no fs jail / no network isolation; `bash` reads sibling users' sandbox slices, `network:false` is advisory. Tool description claims "No network". | `code_execution/executor.py`; `code_execution_action.py:99-116,154` |
| H17 | Memory | **Backup/export sees only chain head.** `export_memory`/`memory_healthcheck` use `conv.nodes(node=Interaction)` (default `direction="out"`); Conversation is edge-connected only to the head → exports 1 interaction/conversation, drops the rest. Silent data loss on the documented backup path. | `memory/manager.py:1173,577-582`; `conversation.py:324` |
| H18 | Memory | **Proactive double-claim across workers.** `claim_proactive` pending→active is check-then-persist; per-process monitor lock only. Without Redis/Dynamo, two workers dispatch the same proactive message twice; lease id written but never compared. | `task_store.py:958-981`; `task_monitor.py:288-297` |
| H19 | CLI/Repair | **Graph repair self-destructs on multi-tick reattach.** `reattach_ctx` stores live Node objects in `state["cursor"]`; a tick deadline mid-phase persists them and hits `json.dumps(cursor)` with no `default=` → `TypeError` → handler **deletes RepairState and re-raises** → every repair restarts, re-runs destructive dedupe forever, never reaches delete/done. Resume rehydrates dicts → handlers throw → reattachable nodes fall to delete phase = data loss. | `core/graph_repair_job.py:960-963,1442`; `graph_repair.py:317-331` |
| H20 | CLI | **Repair cron scheduler started on a dead loop.** `AsyncIOScheduler.start()` runs inside `asyncio.run(pre_startup_bootstrap)`; that loop closes before `server.run()`. Logs "started", never fires. Same loop-boundary hazard for Action `on_startup` asyncio/aiohttp state and bootstrap DBLog writes. | `core/startup.py:89-101`; `cli/server.py:210` |
| H21 | CLI/Logging | **Log retention never enforced + PII accumulates.** `App.log_retention_days` read by nothing; jvspatial's is "reserved, not implemented". INTERACTION level force-added regardless of config; full utterance/response/params persisted each turn. Unbounded undeletable PII store. | `core/app.py:65`; `cli/server_config.py:443`; `interact/webhook_pipeline.py:238-269` |

### HIGH — abuse / auth surface (interact)

| # | Defect | Anchor |
|---|--------|--------|
| H22 | **Rate limiter spoofable by default.** `JVAGENT_TRUST_PROXY_HEADERS` defaults true; attacker-random `X-Forwarded-For` → every request a new bucket, 60/min never trips. Create path spawns User+Conversation+LLM turn per request → unbounded spend/DB growth. | `interact/rate_limiter.py:293-295` |
| H23 | **Rate limiter per-process** — useless on multi-worker (effective limit N×). No shared store. | `interact/rate_limiter.py:32-42,68` |

### MEDIUM (representative — full list in reviewer outputs)

- **M1** `log`-mode tokenless resume returns victim history **and mints a valid durable Mode B token** bound to the conversation; survives the flip to `required`. `session_token.py:426-442`, `endpoints.py:176-207`.
- **M2** Session-existence oracle in `required` mode (401 vs 200 on resume of nonexistent session). `session_token.py:420-442`.
- **M3** Auth-denial `reason` strings (`bind_user_mismatch`, `token_expired`) returned in the response body. `endpoints.py:561,316`.
- **M4** Unbounded observation growth — full tool results re-injected every tick, no cap → context overflow → whole budget burned in failing model calls → misleading clarify fallback. `orchestrator tools.py:124-133`, `loop.py:659-661`.
- **M5** Locked-flow dispatch bypasses `tool_call_timeout` + wall-clock deadline; a hung IA blocks the turn while holding the lock. `loop.py:97`.
- **M6** Tool-surface cache bakes in a transient `get_tools()` failure with no TTL → action's tools vanish until restart. `orchestrator_interact_action.py:841-847,914-916`.
- **M7** Runtime pip install: no allowlist/pin (supply chain), per-action install → two actions pinning different versions of one package reinstall each boot; install result discarded. `core/dependency_installer.py:74-116`.
- **M8** Cache TTL mixes naive/aware datetimes → `TypeError` on the interact path when `App.timezone` unset vs set. `core/cache.py:13-23` + `app.py:271`.
- **M9** `development.debug:true` → uvicorn reload with app object (not import string) → `sys.exit(1)` after bootstrap consumed the one-shot `update_mode`. `cli/server_config.py:221-225`.
- **M10** `--purge` no path sanity / no confirmation / guard passes when `JVSPATIAL_ENVIRONMENT` unset (default development). `cli/server.py:59-144`.
- **M11** Orphaned `active`/`pending` task (crash/serverless timeout) has no lease/TTL → `conversation_has_blockers` true forever → proactive dispatch + engagement blocked permanently. `task_eligibility.py:102-114`.
- **M12** Un-locked pruning path (`_ensure_conversation_interaction_limit`) races the locked append+prune → dangling edges / deleted-node refs. `memory/manager.py:317-342`.
- **M13** TaskMonitor dry-run always errors (`pending → completed` rejected, swallowed as "dispatch failed"). `task_monitor.py:315-321`.
- **M14** `agent.interaction_limit=0` doesn't disable pruning on conversations that previously synced a positive limit (violates documented contract). `conversation.py:330-337`.
- **M15** Stale `StepHandle` for/else re-appends orphaned steps after `set_plan`/`sync_plan` regenerate ids → phantom pending work. `task_store.py:737-753`.
- **M16** Endpoint-unregister fallback mutates registry during iteration → `RuntimeError`, remaining endpoints leak. `action/base.py:553-569`.
- **M17** `web_fetch` `max_bytes` enforced after full download (memory DoS) + DNS-rebinding TOCTOU. `web_fetch_action.py:163-164,86`.
- **M18** `stage_skill` path traversal + `rmtree` on unvalidated `name`. `code_execution_action.py:127-134`.
- **M19** SSE duplicate delivery + dead shutdown check (subscribe-before-replay; `done` only set in finally). `response/streaming.py:44-85`.
- **M20** WhatsApp pairing QR served `auth=False` (security-by-obscurity on a credential-equivalent). `whatsapp/endpoints.py:299-344`.
- **M21** `_recount_agent_statistics` counts `Agent.find({})` globally — wrong in shared/embedded DBs. `app_loader.py:453`.
- **M22** Declarative-bootstrap failure falls back to `_manual_bootstrap` mid-error, creating a default App and masking partial installs; also writes `App._cached_app` directly, bypassing the thread guard. `cli/bootstrap.py:60,91,105`.
- **M23** Module-unload prefix collision — deregistering `foo` unloads sibling `foo_bar`. `action/base.py:640-641`.
- **M24** `.env` loaded with `override=True` beats operator-injected env, inverting documented precedence. `cli/server.py:52`.

Lower-severity items (equal-timestamp chain infinite loop `interaction.py:741-756`; GET endpoints minting Users; EvidenceLog dead+unbounded; crash-window headless conversation; `_events`/non-proactive-task unbounded growth; provider `Retry-After` clamp; fire-and-forget task GC; serverless `AWS_LAMBDA_FUNCTION_NAME` false-detect; etc.) are enumerated in the per-subsystem reviewer outputs.

---

## 2. Improvement plan (phased)

### Phase 0 — Stop the bleeding (small, high-value, low-risk)
1. **Rate-limiter fail-safe** (H22): default `JVAGENT_TRUST_PROXY_HEADERS=false`; only honor `X-Forwarded-For` when an explicit trusted-proxy CIDR/hop-count is configured. Ship as a security patch.
2. **Reply endpoint authz** (H10/H11): bind `session_id` to the authenticated identity on subscribe; add `roles=["admin"]` **or** session-ownership on publish. Regression test both.
3. **Stop leaking auth internals** (M3): move `reason`/`err` to server logs; return generic codes to clients.
4. **`log`-mode must not mint tokens or return history to a denied identity** (M1): in `log` mode, observe-and-record only — never issue a Mode B token or resume a conversation for a tokenless/failed identity.
5. **Guard `development.debug`** (M9): reject `debug:true` with multi-worker/app-object run, or wire reload via import string. Fail at validate, not after bootstrap.
6. **`--purge` safety** (M10): require an app-scoped, DB-shaped target path + interactive confirmation (or `--yes`); tighten the dev-mode guard to require an explicit opt-in, not merely unset env.

### Phase 1 — Identity & uniqueness substrate (fixes the duplicate-singleton class: C1–C3, C5, C6, H14, C3-agent)
1. **Canonical identity = `(agent_id, namespace, label)` for actions, `(namespace, name)` for agents.** Correct the compound-index key shapes (add `namespace`).
2. **Adapter-agnostic uniqueness.** Since `json` (and any adapter without native unique constraints) can't enforce it, add an application-level **upsert-by-identity** helper used by every ensure/create path: query by full identity tuple *without* relying on subclass import (query raw by `context.*` fields / explicit `entity`), and create only inside a held lock (see Phase 2). Replace the `find_one`-then-`create` pairs in `install_agent`, `_ensure_actions_node`, `_ensure_memory_node`, `_ensure_agents_node`, `register_action`, `Memory.get_user`, `get_session`.
3. **Singleton check must not depend on imported subclasses** (C1): resolve existing singletons via raw records (as `_reconcile_actions` already does) rather than `Action.find_one`.
4. **Run-mode dedupe/reconcile** (C1): run a lightweight identity-reconcile on every boot (not only `--update`) so a plain restart converges duplicates instead of accumulating them.
5. **Fix `get_model_action` fallback** (H14): use `get_action_by_base_class(LanguageModelAction)`; add an integration test with a non-OpenAI-only agent.

### Phase 2 — Locking & concurrency (C4, C7–C9, H15, H18, M12)
1. **Distributed bootstrap lease** (C4): wrap graph bootstrap in the existing distributed lock (or a dedicated bootstrap lease) so only one worker/replica mutates the graph at a time; others wait and read.
2. **Turn-lock lease renewal** (C7): add a heartbeat that extends the lease while the turn runs, or make the lease auto-renew until release; set the lease TTL above the orchestrator's max plausible turn and enforce a real `max_duration_seconds`.
3. **Contextvar lock-holder must not cross into background tasks** (C8): reset/clear `_lock_holder` when spawning `run_in_background` tasks (explicit `contextvars.Context` copy with the holder cleared), or key reentrancy on something task-local.
4. **Eliminate whole-document lost updates** (C9/H15/H18): move per-turn mutable state (TaskStore, counters, usage, `_last_result`) off the shared cached node instance and/or apply field-scoped atomic updates; compare-and-set the proactive claim lease id on dispatch.
5. **Lock the pruning path** (M12): take `conversation_mutation_lock` in `_ensure_conversation_interaction_limit`.

### Phase 3 — Orchestrator hardening (H12, H13, M4–M6, M11)
1. **Trust boundary on the directive contract** (H12): only honor `next_tool`/`response_directive` from an allowlist of trusted skill-spec/first-party tools; never from MCP/third-party observations.
2. **Break the permanent lock traps** (H13): `locked_denied`/`locked_silent` must either release the control-task or count toward the error-streak escape after N turns.
3. **Cap observations** (M4): truncate re-injected tool results to a byte budget; keep a pointer, not the whole payload.
4. **Timeout/deadline on locked-flow dispatch** (M5): apply `tool_call_timeout` + wall-clock deadline to the locked path too.
5. **Tool-surface cache TTL / don't cache partial failures** (M6).
6. **Orphaned-task TTL/lease** (M11): give SKILL/turn-lock tasks a lease so a crash can't block proactive dispatch and engagement forever; sweep expired.

### Phase 4 — Repair, logging, lifecycle (H19–H21, M7, M8, M13–M24)
1. **Repair cursor must be serializable** (H19): store node **ids**, not live objects, in `state["cursor"]`; add `default=` to `json.dumps`; add a multi-tick reattach-resume test. Reconsider "delete RepairState on error" — it turns a transient failure into a restart loop.
2. **Scheduler on the server loop** (H20): start the repair scheduler (and any asyncio `on_startup` resources) inside the uvicorn lifespan, not the throwaway bootstrap loop. Audit every Action `on_startup` for loop-bound state.
3. **Implement log retention + reduce PII** (H21): a purge job honoring `log_retention_days`; make INTERACTION-level logging opt-in; expand `sanitize_visitor_data_for_log` coverage.
4. **Runtime pip install** (M7): default-off in production, allowlist + pinning/hashing, dedupe across actions, honor the install result.
5. **Cache clock** (M8): use a single aware-UTC clock for cache TTLs (don't route through `App.now()`).
6. Remaining M-items: batch by subsystem into follow-up PRs (TaskMonitor dry-run, interaction_limit=0, StepHandle for/else, endpoint-unregister iteration, web_fetch streaming cap + SSRF re-resolve, stage_skill traversal, SSE dedupe, WhatsApp QR auth, global agent recount, manual-bootstrap fallback, module-unload prefix, `.env` override).

### Phase 5 — Test debt (blocks regressions in all of the above)
- **Concurrency suite** (currently zero): duplicate User/Conversation/action creation; lock-lease expiry mid-turn; contextvar reentrancy; proactive double-claim; concurrent turns on one conversation.
- **Pruning regression suite**: the two test files `memory/CLAUDE.md` references **do not exist**; `_prune_old_interactions` (cap, chain rewiring, `last_interaction_id`, limit-sync) is essentially untested.
- **Interact auth**: `log`-mode no-leak, streaming-path identity guard parity, rate-limiter spoofing/isolation.
- **Repair multi-tick resume**, **loop-lifecycle** (scheduler actually fires after `run_server`), **reply endpoint authz**, **`get_model_action` fallback**, deregistration cleanup paths.

---

## 3. Suggested sequencing for review

- **Ship now as isolated security patches:** Phase 0 items 1–4 (rate limiter, reply authz, reason leak, log-mode token). Each is small and independently testable.
- **One design ADR** for Phase 1+2 (identity + locking substrate) — it changes contracts in `core/CLAUDE.md` and `memory/CLAUDE.md` and supersedes the "compound index rejects on save" claim, which is false on the default adapter.
- **Phase 3/4** as per-subsystem PRs behind the substrate work.
- Treat **Phase 5** as acceptance criteria, not a follow-up: the high-severity band is entirely untested today.
