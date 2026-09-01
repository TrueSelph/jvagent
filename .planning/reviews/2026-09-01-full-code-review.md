# jvagent Full Code Review — 2026-09-01

**Scope:** Entire `jvagent/` tree (~525 Python files, ~139k LOC)  
**Method:** Eight parallel subsystem reviewers + independent spot-verification of top CRITICAL claims + static gates  
**Branch:** `main` (clean working tree at review time)

---

## Executive summary

Every enforced static gate is green — flake8 clean, `pre-commit run mypy --all-files` passes, and `pytest tests/` exits 0 (3,558 tests collected). Despite that, the review surfaced **~147 verified defects across eight subsystems, including 13 CRITICAL**. The gates are green because they cannot see this class of bug: deferred-save persistence outside the request path, queue-based walker semantics misunderstood at call sites, cross-turn mutable state on shared Action instances, and a mypy hook that runs without jvspatial installed so the entire jvagent↔jvspatial boundary collapses to `Any`.

The codebase is well-engineered where invariants are mechanically checkable (async-only I/O, constant-time HMAC on POST webhooks, bounded orchestrator tick loop, HTTP client lifecycle in the model layer, static-server path containment). It is weakest exactly where correctness depends on a human remembering a documented pattern — per-loop locks, deferred-save flush, conversation mutation locking, and path validation before filesystem writes.

**Highest immediate risk:** simulated streaming corrupts all non-Latin replies in transit and at rest; the shared action cache key crashes turns or silently drops the model action; Facebook Messenger can exfiltrate Page tokens and route replies to the wrong user; scaffold/CLI path traversal allows arbitrary writes and deletes; graph repair cannot complete its orphan-reattach phase and can strip live edges during edge-sync.

**Process note:** `graph_repair_job.py`'s `reattach_ctx` serialization bug was already documented as H19 in [2026-07-16-core-review.md](2026-07-16-core-review.md) and remained live at review time — review findings are being written down but not always reaching the fix queue.

---

## Static gate results

| Gate | Result | Notes |
|------|--------|-------|
| **flake8** (`flake8 jvagent/ --config=.flake8`) | **PASS** (0 issues) | Style/format clean |
| **pre-commit mypy** (`pre-commit run mypy --all-files`) | **PASS** | Hook pins mypy 1.10.1; `additional_dependencies` lists only `types-PyYAML` and `types-requests` — no jvspatial, pydantic, or httpx |
| **pytest** (`pytest tests/ -p no:randomly`) | **PASS** (exit 0) | 3,558 tests collected |
| **mypy dev venv** (`mypy jvagent/`) | **FAIL** | **270 errors in 48 files** (local mypy 1.20.1 with jvspatial installed) |

**Mypy gap:** CLAUDE.md asserts the pre-commit hook and bare `mypy jvagent/` are aligned; they are not. Inside the isolated pre-commit env every jvspatial type is `Any`, so boundary errors like `"Object" has no attribute "name"` (`cli/agent_commands.py:248`) and `AuthConfig` keyword mismatches (`cli/server_config.py:418`) are invisible to the enforced gate. Representative errors live precisely at the jvagent↔jvspatial boundary — the area most likely to hide real runtime bugs.

---

## CRITICAL findings (13)

| # | Location | Description | Fix |
|---|----------|-------------|-----|
| C1 | `jvagent/action/response/chunking.py:60` | `encoding.decode([tid])` decodes each tiktoken id in isolation; multi-byte UTF-8 characters spanning multiple BPE tokens become U+FFFD. Verified: `"Bonjour 世界"` → `"Bonjour ��界"`, Devanagari and emoji also corrupt. Corrupted chunks reach channel adapters and are persisted in `interaction.response`. | Feed `decode_single_token_bytes(tid)` through a UTF-8 incremental decoder; yield only complete characters. |
| C2 | `jvagent/action/interact/interact_walker.py:620` | Action cache key `{agent_id}:enabled` (`core/cache.py:198`) is shared between `on_actions` (writes `InteractAction`s only) and `Actions.get_actions` (writes every enabled `Action` subclass). Whichever writer wins, the walker sorts by `a.weight` — an attribute on `InteractAction` only — causing `AttributeError` swallowed into "Error during traversal" (no reply), or `get_model_action`'s base-class fallback returns `None` for the TTL window. | Separate cache keys (e.g. `{agent_id}:interact:enabled`) or filter with `isinstance(a, InteractAction)` on read. |
| C3 | `jvagent/core/graph_repair_job.py:961` | `_tick_orphans_reattach` stores live `Memory`/`Agent` Node objects in `state["cursor"]["reattach_ctx"]`; `run_repair_session` feeds this to `json.dumps` (no `default=`), raising `TypeError` every orphans_reattach tick. Repair deletes `RepairState` and cannot advance past this phase. Already noted as H19 in 2026-07-16 review. | Keep `reattach_ctx` in a local variable; persist only ids in the cursor. |
| C4 | `jvagent/core/graph_repair_job.py:754` | Per-node "expected" edge set is read from an unrelated fixed scratch window (first `batch_size` rows, `valid_edge` capped at 50,000). Nodes outside that window get empty `expected` and `node.edge_ids` is rewritten to `current & valid_ids`, silently stripping live edges and disconnecting the graph. | Page scratch rows scoped per node; drop the fixed 50k cap. |
| C5 | `jvagent/action/orchestrator/loop.py:227` | `self._turn_prompt_cache` stores per-turn prompt sections on the `OrchestratorInteractAction` node, which `core/cache.py` shares as one Python instance across concurrent turns (60s TTL). One conversation's `session_context` can render into another user's system prompt. | Carry cache on `TurnState`, `ContextVar`, or turn-scoped dict — not on `self`. |
| C6 | `jvagent/action/orchestrator/orchestrator_interact_action.py:2493` | `_normalize` calls `.strip()` on `decision["action"]`, `decision["tool"]`, and `args["name"]` without type checks. Non-string model JSON raises `AttributeError` out of `_run_loop` (no `except`), killing the turn while the flow lock stays active. | Coerce with `x.strip() if isinstance(x, str) else ""` before use. |
| C7 | `jvagent/action/facebook_action/facebook_api.py:275` | `download_messenger_attachment` appends Page access token as `?access_token=` to any URL from the webhook payload; `_messenger_attachment_url` returns `payload.url` for fallback/link attachments — token exfiltration plus SSRF. | Allowlist Meta CDN hosts; send token as `Authorization: Bearer` header. |
| C8 | `jvagent/action/facebook_action/endpoints.py:645` | Messenger POST verifies HMAC but never checks payload `entry[].id` against `fb_action.page_id`. PSIDs are page-scoped; replies can go to the wrong user. | Skip events whose page id ≠ configured `page_id`. |
| C9 | `jvagent/action/facebook_action/facebook_action.py:359` | When neither `verify_token` nor `FACEBOOK_VERIFY_TOKEN` is configured, `hmac.compare_digest(b"", b"")` returns True — open GET challenge endpoint. | Return failure immediately when expected token is empty. |
| C10 | `jvagent/skills/artifact_handler/scripts/custom_tools.py:938` | Model-supplied `doc_name` bypasses `{user_id}_` namespacing; `delete_document` removes by bare name with no access metadata check — cross-user clobber/delete. | Always derive stored name from `_vault_doc_ids(user_id, ...)`; verify `access` metadata before delete. |
| C11 | `jvagent/cli/skill_commands.py:105` | `skill create-* --force` does `shutil.rmtree(agent_dir / "skills" / ns.skill_name)` with unvalidated `skill_name` — `../../../..` deletes outside app root with no confirmation. | Validate skill name; assert resolved path under `agent_dir/skills`. |
| C12 | `jvagent/cli/skill_commands.py:54` | `skill add` builds `skills_dir / ns.skill_name` and writes with no validation — path traversal writes `SKILL.md` outside app root. | Same containment check as C11. |
| C13 | `jvagent/scaffold/operations.py:542` | `create_agent_in_app` splits unvalidated `agent_spec` into namespace/id and writes under `app_root/agents/` — `../../evil/bot` escapes app root (`create_app:466` same hole). | Strict identifier charset in `parse_agent_spec`; assert resolved dir under `app_root/agents`. |

---

## HIGH findings (by subsystem)

### Core (`jvagent/core/`)

| Location | Description | Fix |
|----------|-------------|-----|
| `conversation_health/service.py:119` | Concurrent turns for same agent lose counter updates on shared `day_buckets` (read-modify-write across awaits). | Per-agent lock or atomic increment deltas. |
| `graph_repair.py:501` | Interaction chain walk has no visited set or deadline — cyclic corruption hangs repair tick forever. | Track seen ids; respect tick deadline. |

### Orchestrator (`jvagent/action/orchestrator/`)

| Location | Description | Fix |
|----------|-------------|-----|
| `continuation.py:247` | Abandon sweep failure resets streak to zero — wedged turn-lock when cancel path keeps failing. | Pop streak only on successful cancel branch. |
| `continuation.py:340` | `clear_soft_abandon_strike` pops in-memory only, never persists — stale strike ≥2 triggers immediate abandon after reload. | Make async; `await conversation.save()` after pop. |
| `tools.py:71` | `wrap_action_tool` ignores `Tool.access_label` from decorator — `@tool(access_label="secret")` bypasses AccessControl. | Default param to `getattr(tool, "access_label", None)`. |
| `catalog.py:45` | `_TOOL_SURFACE_CACHE` process-global, no TTL/loop identity — stale Action-bound tools after cache refresh or warm start. | Include loop id in key; invalidate with action cache. |

### Memory (`jvagent/memory/`)

| Location | Description | Fix |
|----------|-------------|-----|
| `task_store.py:765` | `_persist()` uses `conversation.save()` on DeferredSaveMixin node; TaskMonitor paths never flush — CAS lease and terminal transitions not durable; tasks re-dispatched. | End mutations with `flush_deferred_entities` or `await conversation.flush()`. |
| `conversation.py:604` | `_prune_old_interactions` deletes nodes immediately but persists counters via deferred `save()` — repair path leaves inflated counts and dangling `last_interaction_id`. | `await self.flush()` after prune. |
| `conversation.py:332` | `_add_interaction_unlocked` never re-reads Conversation after lock — multi-worker chain fork. | Re-fetch inside lock (like `claim_proactive`). |
| `conversation.py:484` | `_reap_artifacts_for` disconnects registry and deletes file before node delete; swallowed failure leaves orphaned Artifact. | Delete node first; reconnect on failure. |
| `task_store.py:770` | `_persist_task` no-ops when task id missing from list — terminal transition dropped, turn lock wedged. | Log/append missing entry or raise. |
| `manager.py:1060` | `_recalculate_counters` uses deferred save — repair endpoint reports success but nothing persisted. | Flush conversation after repair. |
| `manager.py:360` | Bulk prune path skips `conversation_mutation_lock` — races with live `add_interaction`. | Wrap per-conversation prune in lock. |

### Action framework (`jvagent/action/`)

| Location | Description | Fix |
|----------|-------------|-----|
| `base.py:944` | Same shared cache as C2 from model-action lookup side — silent missing model after interact cache wins. | Separate cache keys. |
| `interact/interact_walker.py:534` | `_finalize()` runs immediately after `visit()` enqueue — end-of-turn signal and buffer pop at *start* of turn. | Finalize after queue drain, not in `on_agent`. |
| `interact/interact_walker.py:521` | Turn lock released when enqueue returns — no action executes under lock despite docstring. | Hold lock across queue drain. |
| `parameters.py:535` | Plugin/skill can set `ambient: true, inviolable: true` — claims safety floor meant for CORE_PARAMETERS only. | Strip `ambient`/`inviolable`/`source` from non-core params. |
| `parameters.py:576` | Inviolable floor winner is first-wins by insertion order — genuine core rule dropped silently. | Prefer known CORE_PARAMETERS identity. |
| `loader/action_loader.py:1246` | Filesystem-discovered actions loaded regardless of `agent.yaml` — undeclared directory = enabled action. | Skip metadata absent from config unless explicit flag. |
| `actions.py:130` | `register_action` persists and connects before `on_register()` — hook failure leaves half-registered enabled action. | Hook before connect/save, or rollback on failure. |
| `interact/rate_limiter.py:69` | Module-level `asyncio.Lock()` on import — warm-start `RuntimeError` on public interact. | Per-loop lazy lock (`core/app.py:100` pattern). |

### Model / response / reply

| Location | Description | Fix |
|----------|-------------|-----|
| `response/streaming.py:102` | Shared `acc.message_id` across chunks makes SSE dedup drop live content on reconnect mid-stream. | Dedup on `(id, message_type, sequence)`. |
| `response/response_bus.py:961` | Adapter retry re-sends entire message after partial WhatsApp delivery — duplicate messages. | Retry only when adapter reports nothing delivered. |
| `response/response_bus.py:81` | Module-level `_agent_bus_lock` — cross-loop failure after contention. | Per-loop lock dict. |
| `embed/interact.py:18` | Same import-time lock on interact task registry. | Per-loop lazy lock. |
| `reply/reply_action.py:665` | `generate()` failure after chunks published sends fallback too — user sees truncated + full reply. | Close stream instead of fallback when content already sent. |

### Channels (WhatsApp, Facebook, SentDM, MCP)

| Location | Description | Fix |
|----------|-------------|-----|
| `sentdm_broadcast/sentdm_broadcast_action.py:1530` | Webhook URL with plaintext `api_key` logged at INFO on every reconcile. | Log path only, strip query string. |
| `whatsapp/whatsapp_action.py:362` | Hardcoded `"jvconnect"` verify token for all meta-provider agents. | Always derive from agent secret; 403 when empty. |
| `whatsapp/modules/base.py:347` | POST retries on send — duplicate WhatsApp messages after timeout. | Retry GET/HEAD only, or idempotency marker. |
| `utils/meta_webhook_dedup.py:94` | Sync Redis client in async webhook handlers — blocks event loop. | Use `redis.asyncio`. |
| `whatsapp/modules/base.py:59` | ClassVar `asyncio.Lock()` at import — ConnectionPoolManager breaks on warm start. | Per-loop lazy lock. |
| `mcp/client.py:155` | `streamable_http_client` yields 3-tuple unpacked into 2 names — transport dead. | Unpack three values. |
| `mcp/client.py:158` | `ClientSession` never entered as async context manager — initialize hangs. | `enter_async_context(ClientSession(...))`. |
| `mcp/mcp_action.py:870` | Remote tool result unbounded in prompt — context blowout / injection. | Truncate with untrusted-data delimiter. |
| `mcp/mcp_action.py:816` | Remote tool name/description/schema trusted verbatim — prompt injection. | Length-cap and strip control chars. |
| `mcp/mcp_action.py:683` | Per-user sandbox failure silently falls back to shared default client — cross-user FS access. | Propagate error as tool failure. |

### Interview / skills

| Location | Description | Fix |
|----------|-------------|-----|
| `interview/session.py:157` | `clear_interview_context` clears entire `conversation.context` — wipes artifact vault and pending ingest jobs. | Delete only interview-owned keys. |
| `interview/engine.py:1786` | Park path clears session even when `park_task` returned False — all answers discarded. | Clear only on successful park. |
| `interview/engine.py:638` | `_under_extracted_candidate_keys` rejects entire batch via server-side regex — valid fields never stored. | Drop heuristic or store valid fields + non-blocking nudge. |
| `interview/hooks.py:555` | Any `SKILL.md` gets `custom_tools.py` exec'd in-process with no trust tier — arbitrary code execution. | Trusted-source allowlist for executable skills. |

### CLI / scaffold / tooling

| Location | Description | Fix |
|----------|-------------|-----|
| `bundle/dockerfile_generator.py:141` | Pip deps from `info.yaml` interpolated unquoted into Dockerfile RUN — shell injection at build. | PEP 508 regex validation before emit. |
| `bundle/Dockerfile.base:4` | `COPY . /var/task/` with no `.dockerignore` — `.env` and local DB baked into image. | Generate `.dockerignore`; narrow COPY. |
| `cli/main.py:108` | `_first_app_root_path` swallows `--dir` value and skill validate path — documented CLI invocations break. | Stop scanning after subcommand; skip flag values. |
| `logging/retention.py:16` | Log purge only called from TaskMonitor — apps without it grow logs DB unbounded. | Startup/periodic purge independent of TaskMonitor. |
| `cli/main.py:175` | `--update --source` destructive sync with no confirmation — can be armed via persisted `App.update_mode`. | Interactive yes + log affected agents first. |

---

## MEDIUM findings (condensed)

| Subsystem | Location | Issue | Fix (one line) |
|-----------|----------|-------|----------------|
| Core | `graph_repair_job.py:1089` | Empty-page dup_prepare counts full key — dedupe no-ops | Strip `\|edge_id` suffix before counting |
| Core | `graph_repair_job.py:1105` | Deadline break skips edges but advances cursor past them | Advance only to last processed edge |
| Core | `conversation_health/config.py:77` | Reads `app.yaml` from cwd not app root | Pass `get_app_root()` |
| Core | `conversation_health/service.py:86` | Sync YAML parse every scored turn | Memoize config at startup |
| Core | `conversation_health/service.py:146` | Loads full conversation for tail only | Bounded tail fetch |
| Core | `cache.py:171` | Naive/aware datetime mix in TTL | Always aware or use monotonic |
| Core | `profiling.py:185` | Import-time asyncio.Lock | Per-loop lazy lock |
| Core | `callback.py:89` | Blocking DNS in async webhook path | `getaddrinfo` in executor |
| Core | `conversation_health/state.py:43` | get_or_create race duplicates state nodes | Per-agent creation lock |
| Core | `conversation_health/endpoints.py:511` | deep_review unbounded sequential LLM calls | Cap turns or defer |
| Core | `app_loader.py:365` | break in child count wrong tie-breaker for App dedup | Finish loop before return |
| Core | `graph_repair_handlers.py:180` | Unbounded BFS per orphan | Node cap + deadline check |
| Orchestrator | `catalog.py:48` | Tool surface hash omits MCP/skills config | Add to hash parts |
| Orchestrator | `loop.py:439` | Task-lock rebinds tools/visible to new objects closures ignore | Restrict in place |
| Orchestrator | `continuation.py:218` | Context RMW without conversation lock | Acquire lock around mutations |
| Orchestrator | `orchestrator_interact_action.py:3278` | Unbounded history resent every tick | Per-statement char cap |
| Orchestrator | `loop.py:556` | drain-reply skips plan finalize | Finalize on early-return paths |
| Orchestrator | `uploads.py:149` | 20 concurrent vision calls per turn | Semaphore or background task |
| Memory | `conversation.py:569` | Prune disconnects head before connecting new | Connect new head first |
| Memory | `conversation.py:227` | Missing head edge creates second head | Fallback newest-interaction query |
| Memory | `task_store.py:842` | `_save_tasks` drops unknown legacy keys | Merge unknown keys on serialize |
| Memory | `task_store.py:887` | Unlocked task list RMW | conversation_mutation_lock |
| Memory | `task_store.py:1111` | No production caller for terminal task sweep | Call from maintenance pass |
| Memory | `task_store.py:1149` | Naive datetime breaks sweep subtract | Normalize to UTC-aware |
| Memory | `conversation.py:376` | Unlocked Artifacts branch create — duplicate branches | Create under lock |
| Memory | `conversation.py:675` | Hydration failures swallowed at DEBUG | WARNING with row id |
| Memory | `endpoints.py:24` | `_memory_tags` returns keys not values | `dict(tags)` |
| Memory | `endpoints.py:180` | Backend outage returns 200 empty list | Propagate exception |
| Action fw | `interact/conversation_lock_manager.py:20` | Import-time lock on webhook path | Per-loop lazy lock |
| Action fw | `dependency_installer.py:115` | Runtime pip with no timeout, git URLs allowed | Default off, timeout, PyPI only |
| Action fw | `loader/action_loader.py:118` | Failed pip install ignored | Propagate or fail boot |
| Action fw | `loader/module_loading.py:98` | Broken module left in sys.modules | Pop on failure |
| Action fw | `interact/interact_walker.py:576` | Unconditional debug-only action hydration every turn | Guard with isEnabledFor(DEBUG) |
| Action fw | `actions.py:35` | Per-instance lock ineffective across graph re-fetches | Module-level per-loop registry |
| Model | `response/response_bus.py:405` | Failed delivery still written to interaction.response | Branch on adapter return |
| Model | `response/response_bus.py:397` | fail_fast filter failure silent while SSE sent chunks | Raise or return status |
| Model | `model/base.py:145` | Retry-After without jitter — thundering herd | Positive-only jitter |
| Model | `model/base.py:102` | Non-idempotent POST retried on timeout/5xx | Idempotency key when supported |
| Model | `model/base.py:155` | No overall retry deadline (~15 min worst case) | Monotonic total deadline |
| Model | `language/openai/openai.py:355` | Unguarded `choices[0]` on 200 error bodies | Check error key first |
| Model | `reply/endpoints.py:306` | Poll pops private queue — message loss | Bus drain API with activity update |
| Model | `response/response_bus.py:307` | Session queue eviction silent | Log warning + gap marker |
| Model | `response/response_bus.py:379` | `_get_now()` → DB read per streamed token | Cache timezone per publish |
| Model | `embedding/base.py:71` | Shared node fields race concurrent embed() | Pass through locals to track_usage |
| Channels | `sentdm/endpoints.py:85` | Missing timestamp accepts replay | Require timestamp window |
| Channels | `sentdm/endpoints.py:37` | Process-local webhook dedup | Redis-backed dedup |
| Channels | `whatsapp/meta_api.py:320` | Phone filter skipped when id unset | Reject when unset |
| Channels | `whatsapp/whatsapp_action.py:1184` | Reconciled ids not saved | save after reconcile |
| Channels | `facebook/facebook_api.py:911` | HEAD with no timeout, unbounded redirects | timeout + redirect cap |
| Channels | `facebook/facebook_api.py:264` | Attachment download unbounded memory | Stream with max bytes |
| Channels | `whatsapp/modules/base.py:336` | CancelledError swallowed in safe_request | Catch Exception only |
| Channels | `sentdm/sentdm_broadcast_action.py:489` | Unbounded recipient list, no idempotency key | Cap + deterministic key |
| Channels | `whatsapp/wwebjs_api.py:248` | Provider token echoed in session dict | Drop token from result |
| Interview | `artifact_handler_interact_action.py:62` | sys.path insert four levels up | Delete lines |
| Interview | `artifact_handler/endpoints.py:1181` | create_task without ref; mark notified before send | Task set + notify after send |
| Interview | `artifact_handler_interact_action.py:1230` | Re-exec custom_tools every dispatch | Module cache |
| Interview | `scaffold/skill_resolve.py:118` | Frontmatter split on `---` truncates YAML | Line-anchored closing delimiter |
| Interview | `interview/engine.py:1986` | Raw exception in user-facing directive | Generic user message |
| Interview | `interview/flow.py:478` | `pruned_fields` grows unbounded | Cap or remove |
| Interview | `interview/tasks.py:90` | ensure_active_task swallows failures | Error log + propagate |
| Interview | `activation_seed.py:119` | Server-side activation extractor (thin-harness violation) | Remove or amend profile |
| Interview | `artifact_handler/endpoints.py:1175` | Job ready write swallowed — stuck queued | Log + 503 Retry-After |
| CLI | `cli/server_config.py:585` | log_db_uri falls back to mongodb_uri in env | Set only when needed |
| CLI | `core/config.py:279` | Bad env var skips app.yaml layer | Warn and fall through |
| CLI | `scaffold/profile_resolve.py:59` | Profile include path traversal | Reject `..` / separators |
| CLI | `cli/main.py:182` | Global `--yes` strip breaks subcommands | Thread assume_yes to handlers |
| CLI | `cli/bundler.py:53` | Dockerfile overwrite silent | Require --force + backup |
| CLI | `cli/agent_commands.py:115` | Bad bundle path silently uses cwd | Error on invalid path |
| CLI | `messenger/server.py:47` | Default frame-ancestors `*` | Default `'self'` |
| CLI | `logging/endpoints.py:45` | Unbounded log page_size | Hard max + validation |
| CLI | `stress_seed_graph.py:268` | int() parse exits with traceback | Actionable usage message |

---

## LOW findings (condensed)

| Subsystem | Location | Issue |
|-----------|----------|-------|
| Core | `index_bootstrap.py:151` | ConversationHealthState missing from eager index list |
| Core | `graph_repair_job.py:1169` | Dup detection capped at 200 scratch rows per pair |
| Core | `graph_repair_handlers.py:145` | Dead misleading assignment in reattach_ctx |
| Core | `callback.py:187` | Bare pass in try block |
| Core | `graph_repair.py:267` | Direct ctx._remove_from_cache bypasses compat chokepoint |
| Orchestrator | `loop.py:966` | Repeat guard only compares last sig — A/B oscillation |
| Orchestrator | `skills.py:17` | Skill discovery cache no TTL — edit requires restart |
| Orchestrator | `orchestrator_interact_action.py:1` | 3512-line god-object (document separable units) |
| Memory | `manager.py:1107` | Orphan scan uses dead `context.memory_id` filter |
| Memory | `conversation.py:654` | Uses jvspatial private query/deserialize APIs |
| Memory | `interaction.py:10` | Logger imported from action.model.base |
| Model | `model/base.py:502` | Stale httpx client dropped without aclose |
| Model | `language/base.py:757` | Dead _execute_with_retry around _query_stream |
| Channels | `sentdm/endpoints.py:56` | base64 decode without validate=True |
| Channels | `whatsapp/endpoints.py:551` | Unreachable nested is_meta_provider check |
| Interview | `interview/tools.py:266` | Custom tool can run against wrong skill session |
| Interview | `interview/engine.py:2157` | complete closes task before clearing session |
| Interview | `artifact_handler/endpoints.py:543` | Dead trusted-url conditional |
| CLI | `cli/server.py:125` | Purge reports success on partial failure |
| CLI | `messenger/server.py:1098` | sandbox_origin from Host header |
| CLI | `scaffold/profile_stub.py:13` | Ad-hoc profile name sanitization |

---

## Systemic patterns (5 themes)

### 1. Import-time `asyncio.Lock()` (~6+ sites)

Module-level or class-level locks created at import bind to the first event loop that contends them, breaking serverless warm starts with `RuntimeError: ... bound to a different event loop`. The correct per-loop pattern is documented and implemented in `core/app.py:100` but not applied consistently.

**Affected (verified):** `response_bus.py:81`, `embed/interact.py:18`, `interact/rate_limiter.py:69`, `conversation_lock_manager.py:20`, `core/profiling.py:185`, `whatsapp/modules/base.py:59`.

**Fix theme:** One shared helper (e.g. `_get_loop_lock()`) + lint rule; sweep all sites in one commit.

### 2. Deferred-save contract violated outside the request path

`Conversation` is a `DeferredSaveMixin` node; writes batch until `flush_deferred_entities` / `await conversation.flush()`. Only the three interact HTTP paths flush reliably. Background callers — graph repair prune, counter recalculation, entire TaskStore — call `save()` and report success while mutations are discarded.

**Impact:** Proactive tasks claimed and finalized without reaching DB (re-dispatch); repair "fixes" that are no-ops; pruned interactions gone but counters stale.

**Fix theme:** Audit every `conversation.save()` off the interact path; replace with flush or explicit deferred batch + flush at operation boundary.

### 3. Unvalidated user strings → filesystem paths

`agent_spec`, `skill_name`, profile `include`/`extends` are concatenated into paths with no traversal or containment check. Three of thirteen CRITICALs are the same missing guard repeated.

**Fix theme:** Shared `path_safe` / containment helper used by scaffold, skill CLI, and profile resolver before any mkdir/write/rmtree.

### 4. Swallowed exceptions → reported success

Memory user-listing returns HTTP 200 with `total=0` on backend outage; `_send_to_adapter` return discarded; artifact notifications marked delivered before send completes; repair endpoints persist counters via deferred save. Each turns an outage into "no data" or "fixed."

**Fix theme:** Propagate or surface failure; never HTTP 200 on error paths meant for operators.

### 5. Mypy gate structural blind spot

Pre-commit passes; dev venv reports 270 errors. Hook lacks jvspatial/pydantic/httpx stubs, so boundary types are unchecked — precisely where integration bugs live.

**Fix theme:** Add jvspatial (and key deps) to hook `additional_dependencies`, or pin a shared stub package; align CLAUDE.md claim with reality.

---

## Suggested fix order

### Phase 1 — Actively wrong in production (same day)

1. **C1** Streaming chunking corruption  
2. **C2** Action cache key collision  
3. **C5** Cross-turn prompt cache on shared Action instance  
4. **C6** `_normalize` type coercion  

### Phase 2 — Security batch (one shared helper commit)

5. **C7–C9** Facebook attachment token, page-id check, empty verify token  
6. **C10** Artifact doc_name namespacing  
7. **C11–C13** CLI/scaffold path validation (`path_safe` helper)  
8. WhatsApp hardcoded verify token (HIGH, channels)

### Phase 3 — Walker / turn semantics

9. **C2 follow-up:** Walker finalize timing + turn lock scope (`interact_walker.py:521–534`)  
10. MCP client tuple + ClientSession context (HIGH, dead transport today)

### Phase 4 — Persistence / repair

11. **C3–C4** Graph repair reattach serialization + edge-sync window  
12. Memory deferred-save flush audit (TaskStore, prune, counter repair)  
13. HIGH memory races (re-read under lock, prune under lock)

### Phase 5 — Hardening sweeps

14. Import-time lock sweep (pattern 1)  
15. Parameters ambient bypass + floors (HIGH, action framework)  
16. Orchestrator continuation persistence bugs  
17. Log retention without TaskMonitor  
18. Mypy hook dependency alignment  

---

## Fix status — 2026-09-01 fix pass (completed)

**Verification:** `pytest tests/ -q` — **PASS** (exit 0, ~3,558 tests). Working tree: **59 files** changed (+1,144 / −336 lines).

| Item | CRITICAL / theme | Status |
|------|------------------|--------|
| Streaming chunking (`chunking.py`) | C1 | **Fixed** — UTF-8 incremental decode + tests |
| Action cache key collision | C2 | **Fixed** — separate interact cache key |
| Walker finalize / turn lock timing | C2-related HIGH | **Fixed** — `spawn()` override; lock held through traversal |
| Orchestrator `_normalize` + prompt cache | C5, C6 | **Fixed** — type coercion; `ContextVar` prompt cache |
| Facebook empty verify token | C9 | **Fixed** — fail when expected token empty |
| Facebook page-id validation | C8 | **Fixed** |
| Facebook attachment allowlist + Bearer header | C7 | **Fixed** |
| MCP streamable-HTTP client | HIGH (channels) | **Fixed** — 3-tuple unpack + async context manager |
| Artifact `doc_name` namespacing | C10 | **Fixed** — force vault namespacing |
| Skill CLI path validation | C11, C12 | **Fixed** — `path_safe` helper + containment checks |
| Scaffold `agent_spec` paths | C13 | **Fixed** — shared path_safe / parse validation |
| Graph repair reattach + edge sync | C3, C4 | **Fixed** — serializable cursor; per-node scratch paging |
| Shared `path_safe` helper | Pattern 3 | **Fixed** — used by CLI/scaffold |
| CLI `--update --source` confirmation | HIGH | **Fixed** — interactive prompt; `--yes` bypass |
| CLI global `--yes` before subcommand | MEDIUM | **Fixed** — routing skips leading flags; `global_assume_yes` for agent |

### HIGH themes addressed in same pass

| Theme | Files | Status |
|-------|-------|--------|
| Deferred-save flush | `task_store.py`, `conversation.py`, `manager.py` | **Fixed** |
| Skill-only parameter sanitize | `parameters.py` | **Fixed** (action params unchanged — avoids ReplyAction regression) |
| Import-time locks (subset) | `response_bus.py`, `embed/interact.py`, `rate_limiter.py`, `conversation_lock_manager.py`, `profiling.py` | **Fixed** |
| TaskStore conversation lock | `task_store.py` | **Fixed** on create/delete/sweep |
| Orchestrator continuation / tools / catalog | multiple | **Fixed** (batch 2) |
| Conversation health, graph repair medium, model retry jitter, SentDM log redaction, messenger CSP, logging page_size cap | multiple | **Fixed** (batch 3) |

### Still open (~40–50 LOW / structural from tables above)

Most HIGH and MEDIUM items from the 2026-09-01 review are now addressed. Remaining work is mostly LOW severity and structural:

- Orchestrator god-object split (`orchestrator_interact_action.py` ~3500 lines)
- Mypy dev-venv alignment (~270 errors; pre-commit hook still passes with stubbed jvspatial)
- Interview engine decomposition; activation_seed thin-harness violation
- Some channel edge cases (SentDM process-local dedup, Facebook attachment size cap)
- Dependency installer hardening (git URLs, timeout defaults)

*Last verified: 2026-09-01 — full `pytest tests/ -q` green after batch 2 fixes + interaction chain ordering fix + whatsapp test isolation.*

---

## Subsystem health snapshot

| Subsystem | Verdict | Risk concentration |
|-----------|---------|-------------------|
| **Core** | Strong invariants (auth, SSRF callback, App singleton); repair job is fragile | `graph_repair_job.py` paging/state machine |
| **Orchestrator** | Loop bounded; tool dispatch excellent; thin harness mostly honored | Shared caches, continuation persistence, `_normalize` |
| **Memory** | Chain logic sound; scoping good | Deferred save + incomplete locking |
| **Action framework** | Plugin loader functional; interact endpoint hardened | Cache key, walker queue semantics, parameters |
| **Model/response** | Retry bounded; secrets contained; HTTP lifecycle strong | Egress half (chunking, dedup, adapter retry) |
| **Channels** | POST HMAC correct; Meta dedup present | Facebook isolation/token; MCP trust; verify GET handshake |
| **Interview/skills** | State machine terminating; no eval/yaml.load | Artifact naming; context teardown; ungated skill exec |
| **CLI/tooling** | Static servers well hardened; purge guarded | Path traversal; destructive sync; Docker secret bake |

---

## References

- Prior core review: [2026-07-16-core-review.md](2026-07-16-core-review.md) (H19 = C3)  
- Prior once-over: [2026-07-17-once-over.md](2026-07-17-once-over.md)  
- Agent guide: [CLAUDE.md](../../CLAUDE.md)  
- Orchestrator design: [docs/ORCHESTRATOR.md](../../docs/ORCHESTRATOR.md)  
- Thin harness: [docs/thin-harness.md](../../docs/thin-harness.md)

---

*Generated from eight subsystem review agents + lead spot-checks. Spot-verified CRITICALs: chunking (runtime), action cache (source + sort), walker `visit()`=enqueue (jvspatial), Facebook empty digest, MCP tuple arity, skill CLI rmtree path.*
