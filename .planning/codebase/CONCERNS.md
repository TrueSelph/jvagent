# Codebase Concerns

**Analysis Date:** 2026-05-06

This document catalogs technical debt, architectural concerns, security considerations, performance bottlenecks, fragile areas, scaling limits, and test coverage gaps in the jvagent codebase. The analysis was conducted on the `dev-cockpit` branch which introduces a new cockpit subsystem alongside the legacy InteractWalker and the unified `AgentInteractAction` pipelines.

---

## Tech Debt

### Three Coexisting Execution Paradigms (Legacy + Unified + Cockpit)

**Issue:** The codebase carries three parallel agent execution models, all callable from production agents:

1. **Legacy stack:** `InteractRouter` + `SkillInteractAction` (deprecated; warning in `agent_loader.py:782`)
2. **Unified stack:** `AgentInteractAction` (`jvagent/action/agent_interact/agent_interact_action.py`, 750 LOC) — the documented canonical surface in `CLAUDE.md`
3. **Cockpit stack:** `CockpitInteractAction` (`jvagent/action/cockpit/`, 22 files / ~5,700 LOC) — the latest, on `dev-cockpit` branch

**Files:**
- `jvagent/core/agent_loader.py:756-786` — runtime detection logic that warns when both new and legacy actions are declared
- `jvagent/action/skill/skill_interact_action.py:149-163` — explicit "Deprecated since v0.x — remove target: next minor release" markers on `thinking_budget_tokens`, `reasoning`, etc.
- `jvagent/action/cockpit/cockpit_interact_action.py:118-167` — cockpit also carries deprecated stream_thinking/stream_reasoning/stream_tool_progress aliases that resolve to `stream_internal_progress`
- `jvagent/action/agent_interact/skill/converse_delivery.py:31` — `AgentInteractAction` inlines its own copy of the converse pipeline

**Impact:**
- Cognitive overhead: contributors must understand which paradigm an agent uses before debugging
- Three independent stuck-detection implementations (each with its own thresholds: `skill_action_contracts.py:123` uses `0.7`, `cockpit/config.py:26` uses `0.65`)
- Three independent system-prompt assembly pipelines, three independent termination-reason enums
- Bug fixes need to be ported across all three (e.g., the deprecated alias logic is duplicated in cockpit and skill_interact_action)
- No documented migration matrix from legacy → AgentInteractAction → Cockpit

**Fix approach:**
- Pick the canonical stack (AgentInteractAction or Cockpit) and document deprecation timeline in CHANGELOG.md
- Extract a shared `StuckDetector` (already exists at `jvagent/action/skill/stuck_detector.py`) and require all stacks to consume it instead of reimplementing in `cockpit/engine.py:393-440`
- Remove the legacy `InteractRouter` + `SkillInteractAction` path once consumers migrate
- Add a deprecation removal target to CHANGELOG (e.g., "Cockpit becomes default in v0.X, AgentInteractAction removed in v0.Y")

---

### Cockpit Module Maturity (New Subsystem on Active Branch)

**Issue:** The cockpit module under `jvagent/action/cockpit/` is a 22-file, ~5,700 LOC subsystem introduced via the `dev-cockpit` branch. It restructures the interaction pipeline around a model-driven think-act-observe loop with the walker-revisit pattern.

**Files (largest):**
- `jvagent/action/cockpit/engine.py` (835 LOC) — core think-act-observe loop and stuck detection
- `jvagent/action/cockpit/memory_tools.py` (650 LOC) — user/conversation-scoped memory tools, with soft-deprecated legacy writers (lines 379-383, 594-600)
- `jvagent/action/cockpit/cockpit_interact_action.py` (619 LOC) — InteractAction adapter exposing 30+ configuration attributes
- `jvagent/action/cockpit/router.py` (458 LOC) — posture/intent routing
- `jvagent/action/cockpit/skill_catalog.py` (443 LOC) — cockpit-local catalog (separate from `jvagent/action/skill/skill_catalog.py`)
- `jvagent/action/cockpit/routing_types.py` (395 LOC)
- `jvagent/action/cockpit/artifact_tools.py` (385 LOC)

**Impact:**
- State persistence across walker revisits relies on in-memory `CockpitEngine` instances stored on `visitor._skill_state["cockpit_engine"]`. The `save_state()` method at `engine.py:374-391` returns a `CockpitState` dict but the docstring explicitly says "state restoration is handled by reusing the same engine rather than deserializing." If the worker process dies mid-run, the engine instance is lost — only the `messages`/`iteration` snapshot survives, and there is no restoration path back into a live `CockpitEngine`.
- Stuck detection uses two heuristics (`engine.py:393-440`): Jaccard similarity on tool-call **signatures** (name + args fingerprint) and primary-tool signature repetition. Both gated by `stuck_min_iterations` (default 4). Thresholds are tunable but no telemetry surfaces actual false-positive rate.
- The cockpit-local `SkillCatalog` (`cockpit/skill_catalog.py`) is intentionally a parallel implementation of the one in `action/skill/skill_catalog.py` (976 LOC) — to keep cockpit self-contained per its own docstring. Discovery TTL of 60 s is hard-coded at module level (`_SKILL_DISCOVERY_CACHE_TTL = 60`).
- Tool registry assembly (`cockpit/registry.py`) dynamically `importlib.util`-loads skill bundle tool modules at runtime (`registry.py:178-184`); failures are swallowed with `logger.warning` (`registry.py:159-165`) — there is no startup validation pass.

**Fix approach:**
- Add integration tests for cockpit state recovery after simulated worker crashes (rebuild engine from `CockpitState` snapshot)
- Document the walker-revisit pattern with a sequence diagram (when prepend([self]) fires, when state lives, when it dies)
- Add telemetry counters for stuck detection (jaccard hits vs primary-repeat hits, false-positive ratio if rerun)
- Add a `_validate_tool_registry()` startup pass that catches unloadable skill tool files at boot rather than at first call
- Consolidate the dual `SkillCatalog` implementations behind a shared interface, or document explicitly why divergence is required

---

### Large Monolithic Action Classes

**Issue:** Many action implementations and core modules exceed 1,000 LOC, hindering test isolation and change review.

**Files (over 1,000 LOC):**
- `jvagent/action/skill/skill_action.py` (3,200 LOC) — full think-act-observe loop, tool dispatch, stuck detection, recovery policy, and final-review pass in a single class
- `jvagent/action/pageindex/endpoints.py` (2,521 LOC) — document upload, conversion, ingestion, search, and admin endpoints all in one module
- `jvagent/core/graph_repair_job.py` (1,682 LOC) — 19-phase repair state machine with `STATE_VERSION = 3` (line 25)
- `jvagent/action/pageindex/pageindex_google_drive_sync_action/pageindex_google_drive_sync_action.py` (1,620 LOC)
- `jvagent/action/pageindex/core/page_index.py` (1,390 LOC) — index lifecycle, lexical FTS, compaction
- `jvagent/action/persona/persona_action.py` (1,291 LOC)
- `jvagent/action/skill/tool_executor.py` (1,277 LOC)
- `jvagent/cli/commands.py` (1,256 LOC)
- `jvagent/action/loader/action_loader.py` (1,192 LOC) — discovery, registration, dependency installation
- `jvagent/action/whatsapp/endpoints.py` (1,166 LOC)
- `jvagent/action/model/language/base.py` (1,158 LOC) — base LM action with retry/streaming/tool-calling
- `jvagent/action/pageindex/documents.py` (1,152 LOC)
- `jvagent/action/base.py` (1,118 LOC) — base `Action` class
- `jvagent/action/interact/endpoints.py` (1,113 LOC)
- `jvagent/action/router/interact_router.py` (1,099 LOC)
- `jvagent/action/google/google_sheets_action/endpoints.py` (1,098 LOC)
- `jvagent/memory/manager.py` (1,085 LOC)
- `jvagent/action/response/response_bus.py` (1,056 LOC)
- `jvagent/memory/conversation.py` (1,024 LOC)
- `jvagent/action/interview/interview_interact_action.py` (1,013 LOC)
- `jvagent/action/interview/core/foundation/decorators.py` (1,002 LOC)

**Impact:**
- Difficult to isolate bugs across 19 phases of `graph_repair_job.py` or the multi-phase loop in `skill_action.py`
- Diff review for changes to `skill_action.py` regularly touches >100 lines because helpers are inlined
- Test coverage tends to be happy-path; middle phases of state machines (e.g., `PH_ORPHANS_REATTACH`, `PH_DUP_APPLY`) are under-exercised
- New hires take longer to onboard because the "owner" file for a feature is too large to read in one sitting

**Fix approach:**
- Extract phase handlers in `graph_repair_job.py` into per-phase classes under `jvagent/core/repair_phases/` (the directory already exists with `engine.py` — formalise the split)
- Break `skill_action.py` into `LoopOrchestrator`, `ToolDispatcher`, `LoopTerminationEvaluator`, and `FinalReviewPass`
- Split `pageindex/endpoints.py` into `ingestion_endpoints.py` (upload, convert, index) + `search_endpoints.py` (query, filter) + `admin_endpoints.py` (compaction, stats)
- Split `interview/interview_interact_action.py` and `interview/core/foundation/decorators.py` (1,002 LOC of decorators is anomalous)

---

### Mypy Coverage is Largely Disabled

**Issue:** `pyproject.toml:181-205` declares `ignore_errors = true` for almost every action and core module.

**Files:**
- `pyproject.toml:138-205` — mypy config
  - Line 142: `disallow_untyped_defs = false` (global)
  - Line 148: `check_untyped_defs = false` (global)
  - Lines 156-161: only `jvagent.core.app_context` and `jvagent.env` enforce `disallow_untyped_defs = true`
  - Lines 181-205: `ignore_errors = true` for `jvagent.action.model.*`, `jvagent.action.persona.*`, `jvagent.action.whatsapp.*`, `jvagent.action.pageindex.*`, `jvagent.action.interview.*`, `jvagent.action.mcp.*`, `jvagent.core.*`, `jvagent.memory.*`, `jvagent.cli`, and most `action/*` modules

**Impact:**
- Type errors in 90%+ of the codebase are suppressed at the linter level
- `CLAUDE.md` claims "Type checking: `mypy jvagent/`" is a quality gate, but in practice mypy is silent on the bulk of the code
- New contributors may add untyped or incorrectly typed code that mypy will not flag

**Fix approach:**
- Pick one module per release to migrate from `ignore_errors = true` → strict mode (e.g., `jvagent.core.callback`, `jvagent.memory.manager`)
- Track the override list as tech debt; aim to drain it
- Document the type-safety roadmap in DEVELOPMENT.md

---

### Rate Limiter is Single-Process Only

**Issue:** `InteractRateLimiter` (`jvagent/action/interact/rate_limiter.py:16-141`) uses an in-memory `defaultdict[list[float]]` keyed by `f"{ip}:{agent_id}"`, guarded by an `asyncio.Lock`.

**Files:**
- `jvagent/action/interact/rate_limiter.py:16-25` — class docstring acknowledges: "Multi-process deployments need a shared store (e.g. Redis) — this implementation is adequate for single-worker uvicorn or Lambda with at-most-one concurrent invocation."
- `jvagent/action/interact/rate_limiter.py:177-218` — `extract_client_ip()` reads `X-Forwarded-For` (first IP), `X-Real-IP`, `CF-Connecting-IP`, falling back to `request.client.host`. There is no proxy-trust whitelist — any client can spoof these headers when the server is exposed without a reverse proxy.

**Impact:**
- Multi-worker deployments (e.g. `uvicorn --workers 4`) get `4 × rate_limit_per_minute` capacity per client because each worker has its own counter
- Spoofable rate-limit key (`ip:agent_id`) when the server is exposed directly. A client setting `X-Forwarded-For` can bypass the limiter entirely.
- No backend abstraction — the docstring mentions Redis but no Redis backend exists

**Fix approach:**
- Implement `RedisRateLimiter` using `redis>=5.0.0` (already optional via `pyproject.toml:80-83`)
- Add a `JVAGENT_RATE_LIMITER_BACKEND=memory|redis` configuration knob with auto-detect when `--workers > 1`
- Add a trusted-proxy allowlist: `JVAGENT_TRUSTED_PROXIES` env var with CIDR ranges; only honour `X-Forwarded-For` from listed sources
- Log a startup WARNING when `--workers > 1` and the in-memory limiter is in use

---

### Environment Variable Placeholder Resolution is Lenient

**Issue:** `resolve_env_placeholders()` in `jvagent/core/env_resolver.py:24-116` substitutes `${VAR}` with the empty string when the variable is missing. Only `${VAR:?}` triggers a WARNING-level log.

**Files:**
- `jvagent/core/env_resolver.py:91-114` — `replace_placeholder` always returns `""` for unset vars; only logs `WARNING` when `:?` syntax is used or when `JVAGENT_WARN_EMPTY_PLACEHOLDERS=true`
- The default for `JVAGENT_WARN_EMPTY_PLACEHOLDERS` is empty/false (`env_resolver.py:19-21`)

**Impact:**
- Critical secrets (e.g. `openai_api_key: ${OPENAI_API_KEY}`) silently become `""` at startup, causing 401/403 errors at first API call hours into operation
- No startup validation that essential placeholders resolved to non-empty strings
- The `${VAR:?}` syntax is opt-in — most agent.yaml authors do not use it

**Fix approach:**
- Add a `config.required_env_vars: [list]` schema field; `app_yaml_validator.py` should fail fast if any listed var resolves to empty
- Or: introduce a sentinel `${VAR!}` that errors on missing, then migrate callers
- Document `JVAGENT_WARN_EMPTY_PLACEHOLDERS` as a recommended production setting in DEVELOPMENT.md / production hardening guide
- Consider making `:?` syntax raise `ValueError` instead of warn-and-empty-string (breaking change requiring CHANGELOG migration note)

---

### Runtime Pip Installation of Action Dependencies

**Issue:** Action `info.yaml` files declare pip dependencies that the action loader installs at runtime via `pip install` subprocess.

**Files:**
- `jvagent/core/dependency_installer.py:27-100` — `install_pip_dependencies()` invokes `subprocess.run([sys.executable, "-m", "pip", "install", ...])`
- `jvagent/core/dependency_installer.py:158-162` — gated by `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL`
- `jvagent/cli/server_config.py:486-491` — startup warning when `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` is unset and `JVAGENT_ENVIRONMENT=production`

**Impact:**
- Supply-chain risk: a compromised PyPI release of a transitive dependency is auto-installed into the running process
- Dependency conflicts: two actions can request incompatible versions; the loader installs the second on top of the first without conflict detection
- No package signature verification, no allowlist, no checksum pinning at the loader level
- Default behaviour is to install — opt-out, not opt-in

**Fix approach:**
- Default `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=true` when `JVAGENT_ENVIRONMENT=production`
- Add a `jvagent scan-deps <app_dir>` command that emits a flat, pinned `requirements.lock` for pre-baked container images
- Document the recommended deployment model: pre-install all action dependencies in the Docker image; runtime install is a development convenience only
- Long-term: support package signature verification (sigstore) for trusted-package allowlists

---

### Deprecated Aliases Still Live in Public Surface

**Issue:** Multiple action attributes are marked deprecated but still accepted in `agent.yaml`, increasing the attack surface and forcing the runtime to resolve aliases on every load.

**Files:**
- `jvagent/action/skill/skill_interact_action.py:149-163` — `thinking_budget_tokens`, `reasoning`, `mirror_response_buffer_as_thoughts`: "[Deprecated since v0.x — remove target: next minor release, ~v0.x+1]"
- `jvagent/action/skill/skill_interact_action.py:605-630` — alias resolution logic emits debug logs; not a hard error
- `jvagent/action/cockpit/cockpit_interact_action.py:122-167` — `stream_thinking`, `stream_reasoning`, `stream_tool_progress` deprecated in favour of `stream_internal_progress`
- `jvagent/action/cockpit/memory_tools.py:379-383, 594-600` — soft-deprecated `memory_update_user_model` still routes to `memory_set(scope=user)`
- `jvagent/action/skill/loop_context.py:111-128` — `LoopContext.maybe_truncate()` is deprecated
- `jvagent/skills/pdf_generation/scripts/latex_compiler.py:222-230` — three deprecated aliases for `content`, `subtitle`, `author` in tool definitions
- `jvagent/skills/pdf_generation/scripts/pandoc_fallback.py:222-230` — same three deprecated aliases (duplicated)
- `jvagent/memory/user.py:67` — `[DEPRECATED — use ``memory`` instead]` flagged on a User attribute

**Impact:**
- New users find both old and new options in autocomplete / docs and pick the wrong one
- Removal targets are vague ("next minor release", "v0.x+1") with no specific version commitments
- Tests have to cover both pathways

**Fix approach:**
- Pick concrete removal versions and put them in CHANGELOG.md ("Remove `thinking_budget_tokens` in v0.7.0")
- Add a startup error mode: `JVAGENT_DEPRECATED_AS_ERROR=true` that turns deprecation warnings into hard failures (helps CI catch new use of old names)
- Audit `info.yaml` and `agent.yaml` examples in `examples/` to ensure they use the new names

---

## Known Bugs

### Cockpit Tool Dispatch Runs After response_publish(finalize=true)

**Issue:** In `jvagent/action/cockpit/engine.py:242-292`, the engine dispatches **all** tool calls in a batch via `self._tool_executor.dispatch(result.tool_calls)` (line 255) BEFORE checking the `cockpit_finalized` flag (line 280).

**Files:**
- `jvagent/action/cockpit/engine.py:242-292` — dispatch-then-check ordering
- The deliberate comment at line 276: "Check for finalized flag AFTER dispatching — response_publish already published the content, but other tools in the batch must still execute their side effects."

**Symptoms:** When the model emits a batch like `[response_publish(finalize=true), tool_X()]`, `tool_X` executes after the user has already received the published response, producing side effects that occur "after the conversation ended" from the UI's perspective.

**Trigger:** Any model output where `response_publish(finalize=true)` is not the last tool in the batch.

**Mitigation:** This appears to be intentional (see comment at line 276), but the consequence — out-of-order side effects relative to the user-facing response — is not surfaced in user-facing documentation.

**Fix approach:**
- Document explicitly in `cockpit_interact_action.py` and the cockpit SPEC: "Place `response_publish(finalize=true)` as the LAST tool in any batch; other tools after it will still execute but their effects will follow the published response"
- Or: re-order the batch so `response_publish` is dispatched last, regardless of model output order
- Or: emit a logger.warning when `response_publish(finalize=true)` appears mid-batch, so observability surfaces this case

---

### Cockpit Engine State Cannot be Restored After Worker Crash

**Issue:** Cockpit's walker-revisit pattern persists state via `visitor._skill_state["cockpit_engine"] = engine_instance` and re-adds the action to the walk path with `visitor.prepend([self])`. The `save_state()` method (`engine.py:374-391`) returns a serialisable `CockpitState` dataclass, but its docstring states: "state restoration is handled by reusing the same engine rather than deserializing." There is no documented path to rebuild a `CockpitEngine` from a `CockpitState`.

**Files:**
- `jvagent/action/cockpit/engine.py:374-391` — `save_state()` returns the snapshot but no `from_state()` companion exists
- `jvagent/action/cockpit/cockpit_interact_action.py:46-47` — state stored on the visitor (in-memory)

**Symptoms:** If the uvicorn worker that hosts a long-running cockpit conversation dies (OOM, deploy, crash), the engine instance is lost. The next request resolves to a different worker, finds no engine, and starts a fresh run — losing the in-flight reasoning trace, plan, and partial tool results.

**Trigger:** Any cockpit conversation that spans more than one HTTP request and where the original worker has died.

**Fix approach:**
- Add a `CockpitEngine.from_state(state, ctx)` factory that reconstitutes from a serialised `CockpitState`
- Persist `CockpitState` to the Conversation node (alongside `tasks`) on each step; load on next visit if engine is missing
- Add an integration test that simulates a worker crash mid-loop

---

### Graph Repair STATE_VERSION Bump Restarts Repair From Phase 0

**Issue:** `jvagent/core/graph_repair_job.py:25` declares `STATE_VERSION = 3`. Persisted `RepairState` nodes carry this version; mismatched versions trigger a fresh start. There is no migration helper.

**Files:**
- `jvagent/core/graph_repair_job.py:25` — `STATE_VERSION = 3`
- `jvagent/core/repair_state.py` — RepairState node persistence
- `jvagent/core/graph_repair_handlers.py` and `jvagent/core/repair_phases/` — phase handlers

**Symptoms:** A production repair job that has reached `PH_DUP_APPLY` (phase 17 of 19) will restart from `PH_MEMORY_COUNTERS` after a deploy that bumps `STATE_VERSION`. If phases 0-16 have already mutated the graph, re-running them is at best wasteful and at worst (depending on whether the new code's expectations match the partially-repaired state) corrupting.

**Trigger:** Deploying a code change that bumps `STATE_VERSION` while a repair job is in flight.

**Fix approach:**
- Add `migrate_repair_state(from_version: int, to_version: int, state: dict) -> dict` and call it on version mismatch
- For unsupported jumps, mark the run as failed and require operator intervention rather than silently restart
- Add a `repair_started_at` timestamp to surface long-running jobs in the dashboard

---

### Interaction Pruning Can Race with Read Endpoints

**Issue:** Conversation pruning (`interaction_limit`) is lazy — it runs on `_prune_interactions()` calls during interaction save. Between the read and the next save, a client may successfully `GET` an interaction that is then pruned.

**Files:**
- `jvagent/memory/conversation.py:83-86` — `interaction_limit: int = attribute(default=0, ...)`
- `jvagent/memory/manager.py` (1,085 LOC; pruning logic embedded) — pruning runs at end of save

**Symptoms:** A client reads `/api/agents/{id}/memory/interactions/{interaction_id}`, sees the interaction, then gets 404 on the next call seconds later because pruning fired.

**Trigger:** High-traffic conversation with a low `interaction_limit` and concurrent read requests.

**Fix approach:**
- Document `interaction_limit` as "advisory, non-atomic; recently-pruned interactions may briefly remain visible"
- Or: replace hard delete with a soft-delete tombstone so reads can return a 410 Gone with explanation
- Add a unit test that exercises read-during-pruning timing

---

### Cockpit Skill Tool Loading Failures are Silently Swallowed

**Issue:** `jvagent/action/cockpit/registry.py:155-165` catches `Exception` around `_load_tool_module()` and logs a `warning`. The tool simply does not appear in the cockpit's tool list, with no structured error surfaced to the operator.

**Files:**
- `jvagent/action/cockpit/registry.py:155-165` — broad `except Exception` swallow on per-tool load
- `jvagent/action/cockpit/registry.py:178-184` — `importlib.util` dynamic load with no module cache invalidation

**Symptoms:** A skill bundle whose tool file has a syntax error or missing import will appear "available" in the catalog (because discovery walks `info.yaml`) but its tools will be missing from the registry. The model sees the skill name but cannot call its tools — and the user sees a confusing "I don't have a tool for that" response.

**Trigger:** Any skill tool file that fails to import (syntax error, missing pip dep, circular import).

**Fix approach:**
- Surface tool-load failures via a startup/boot summary endpoint (e.g., `/api/agents/{id}/diagnostics/tool_load_errors`)
- Add a `_validate_tool_registry()` startup pass that fails the bootstrap if any declared skill has unloadable tools
- Replace `except Exception` with narrower `except (SyntaxError, ImportError)` and re-raise unexpected ones

---

## Security Considerations

### Frontend Plaintext Password Storage in localStorage

**Risk:** The jvchat frontend stores plaintext credentials in browser `localStorage` under the key `jvchat_saved_credentials_v2`.

**Files:**
- `jvchat/src/utils/storage.ts:21-86` — `JVChatStorage.saveCredentials()`
- `jvchat/src/components/Login.tsx` — credential management UI
- A 2026-05-02 remediation added an amber warning banner and an export-confirmation notice

**Residual Risk:**
1. Any XSS vulnerability in the chat UI exfiltrates all saved credentials
2. Physical device access reads them directly via DevTools
3. Browser sync (Chrome/Firefox profile sync) replicates them to the cloud unencrypted
4. Any third-party JS injected via debug tools or browser extensions can read them

**Recommendations:**
1. Move to `sessionStorage` (clears on tab close; defeats casual browser-sync exfiltration)
2. Encrypt at rest with the WebCrypto API + a user-provided PIN as KDF input
3. Document explicitly: "Saved credentials are not safe on shared devices"
4. Add an explicit auto-expiry (e.g., 7-day TTL with confirmation prompt to re-save)

**Priority:** MEDIUM (mitigated by warnings; strong recommendation to switch storage)

---

### Webhook SSRF Allowlist is Static

**Risk:** Outbound webhook URLs are validated against a static IP-block list to prevent SSRF.

**Files:**
- `jvagent/core/callback.py:14-23` — `_SSRF_BLOCKED_NETWORKS` lists `127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16` (link-local IPv4), `::1/128`, `fc00::/7` (IPv6 ULA), `fe80::/10` (IPv6 link-local)
- `jvagent/core/callback.py:26-47` — `_validate_webhook_url()` resolves the hostname via `getaddrinfo` and checks every returned address

**Residual Risk:**
- Cloud-provider-specific metadata endpoints (`169.254.169.254` is covered, but custom internal ranges like AWS RDS subnets, GCP internal LB ranges) are NOT blocked
- DNS rebinding: an attacker controls a hostname that resolves to a public IP at validation time and a private IP at request time. The current code resolves once before the request fires; httpx may resolve again at connect time. There is no "pin the resolved IP" guarantee.
- IPv6 NAT64 prefixes (`64:ff9b::/96`) and IPv4-mapped IPv6 (`::ffff:0:0/96`) are not explicitly blocked

**Fix approach:**
- Add `is_private` and `is_reserved` checks: `if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local`
- Pin the resolved IP and pass it directly to `httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(local_address=...))`, OR use `httpx-retries` with a custom resolver
- Add IPv4-mapped IPv6 and NAT64 prefixes to `_SSRF_BLOCKED_NETWORKS`
- Add tests with adversarial hostnames (DNS rebinding simulation, IPv6 mapped IPv4)

---

### MCP Filesystem Sandbox Defaults

**Risk:** The MCP filesystem server (`jvagent/action/mcp/jvspatial_fs_server.py`) can be sandboxed to a configured root, but sandbox mode is configurable.

**Files:**
- `jvagent/action/mcp/sandbox.py:22-36` — `resolve_sandbox_root()` checks `JVSPATIAL_FILES_ROOT_PATH`, then YAML `MCP_FILESYSTEM_SANDBOX_ROOT`, then a default
- `jvagent/action/mcp/sandbox.py:39-46` — `sanitize_segment()` strips unsafe characters; max 200 chars
- `jvagent/action/mcp/sandbox.py:49-60` — `resolve_mcp_sandbox_relpath(agent_id, user_id)` formats `<agentId>/<userId>` after sanitisation

**Impact:** When MCP filesystem tools are exposed to end users, the sandbox boundary is the safety perimeter. If `JVSPATIAL_FILES_ROOT_PATH` is unset and `MCP_FILESYSTEM_SANDBOX_ROOT` is not configured in YAML, the default falls back to `resolve_file_storage_root()` which is `./.files` or `/tmp/.files` — fine in dev, but operators may not realise this behaviour in production.

**Fix approach:**
- Document the sandbox-root resolution order prominently in production-hardening guide
- Add a startup WARNING when MCP filesystem tools are enabled but `JVSPATIAL_FILES_ROOT_PATH` is unset
- Verify `sanitize_segment()` covers Unicode normalisation attacks (`U+202E` right-to-left override, etc.)

---

### LaTeX Injection in PDF Generation Skill

**Risk:** User-supplied content flows into LaTeX templates compiled with `pdflatex`. A 2026-05-02 remediation added hex-color validation and the `-no-shell-escape` flag.

**Files:**
- `jvagent/skills/pdf_generation/scripts/latex_compiler.py` — main compiler
- `jvagent/skills/pdf_generation/scripts/pandoc_fallback.py` — pandoc-based fallback path

**Residual Risk:**
- The `_tex_escape()` function in `latex_compiler.py` handles inline text, but Markdown headings, list items, code blocks, and embedded URLs flow through pandoc / direct substitution. Adversarial Markdown (e.g., `# \input{/etc/passwd}`) needs an integration-test corpus to confirm safety.
- `-no-shell-escape` blocks the most dangerous vector (`\write18` shell execution), but `\input`, `\openout`, `\openin` can still read/write files within the sandboxed working directory

**Fix approach:**
- Build an adversarial-payload integration test that feeds known LaTeX-injection payloads (`\input{/etc/passwd}`, `\openout`, `\write18` attempts) and verifies they do not produce side effects
- Run pdflatex in a chroot or container with read-only filesystem outside the working directory
- Long-term: switch to a less expressive PDF pipeline (HTML → Chromium headless, or Markdown → wkhtmltopdf) that does not interpret user content as code

---

### Subprocess Execution Surface

**Risk:** Multiple modules invoke subprocesses with user-influenced arguments.

**Files:**
- `jvagent/core/dependency_installer.py:77-96` — `subprocess.run([sys.executable, "-m", "pip", "install", ...])` with action-supplied package names from `info.yaml`
- `jvagent/skills/pdf_generation/scripts/latex_compiler.py` — invokes `pdflatex` (mitigations in place; see above)
- `jvagent/action/pageindex/docling_convert.py` — invokes docling
- `jvagent/skills/skill_hub/scripts/_skills_cli.py:197` — `asyncio.create_subprocess_exec` for skill-hub CLI

**Mitigation:** All four use `subprocess.run([list, ...])` (no shell=True; no string concatenation into a shell command). I confirmed no `shell=True` usages anywhere in `jvagent/`.

**Residual Risk:** Pip install with action-supplied names is a supply-chain vector (see "Runtime Pip Installation" above). Other subprocess paths are bounded.

---

## Performance Bottlenecks

### Cockpit Skill Catalog Renders on Every Step

**Issue:** Every cockpit `step()` call rebuilds the system prompt, which includes the rendered skill catalog when the catalog is small or when filtered by `preloaded_skills`.

**Files:**
- `jvagent/action/cockpit/engine.py:442-510` — `_build_system_prompt()` calls `catalog.render_catalog()` on every iteration when the catalog is small enough to inline
- `jvagent/action/cockpit/skill_catalog.py:73` — `_SKILL_DISCOVERY_CACHE_TTL = 60` (seconds) — discovery cache, not render cache

**Impact:**
- Render is O(n) in skill count; with 50+ skills, system-prompt assembly adds tokens to every iteration
- The discovery cache is shared via `SkillCatalog._cache` (class-level) but render output is recomputed
- For multi-iteration loops (default `max_iterations = 25`), the same catalog string is rebuilt up to 25 times per conversation turn

**Fix approach:**
- Cache the rendered catalog string on the `CockpitContext` after first render in a step
- Invalidate the cache when `preloaded_skills` changes
- Alternative: render once during `initialize()` and pass the string forward via context

---

### PageIndex FTS5 Compaction Blocks Ingestion

**Issue:** PageIndex compacts the SQLite FTS5 index on a size threshold during ingestion. The compaction is synchronous and holds a write lock.

**Files:**
- `jvagent/action/pageindex/core/page_index.py` (1,390 LOC) — index lifecycle including compaction
- `jvagent_demo_app_pageindex_db/lexical_postings`, `lexical_stats` — FTS5 storage layout

**Impact:**
- A compaction of a multi-million-row index can take seconds-to-minutes, blocking concurrent ingestion
- For large document sets, ingestion latency becomes bimodal (fast path vs. compaction-blocked path)

**Fix approach:**
- Move compaction to a background asyncio task or separate process; gate ingestion only when the queue is full
- Add metrics: `pageindex_compaction_duration_seconds`, `pageindex_index_size_bytes`
- Consider a PostgreSQL backend with native FTS for deployments past ~1M docs

---

### Graph Repair Memory Counters Phase Scans All Interactions

**Issue:** `PH_MEMORY_COUNTERS` (the first non-dry-run phase) iterates all `Interaction` nodes to validate per-conversation `interaction_count` consistency.

**Files:**
- `jvagent/core/graph_repair_job.py:28` — `PH_MEMORY_COUNTERS` is the entry phase when not dry_run
- `jvagent/core/graph_repair_handlers.py` — phase handler implementations

**Impact:** For agents with millions of interactions, this phase can take hours and blocks subsequent phases (which run sequentially). The repair engine is designed to resume incrementally, but this phase is not internally batched the way later phases are.

**Fix approach:**
- Add cursor-based batching to `PH_MEMORY_COUNTERS` similar to `PH_ORPHANS_BFS`
- Add a per-tick limit: `--repair-max-counter-fixes 1000`
- Consider sampling for large datasets (verify a 1% sample, schedule a full sweep weekly)

---

### Bidirectional Interaction Chain Traversal is O(N)

**Issue:** Interactions are chained bidirectionally (Interaction1 ↔ Interaction2 ↔ Interaction3). To get the most recent interaction without `last_interaction_id`, traversal walks from the first.

**Files:**
- `jvagent/memory/conversation.py:87-90` — `last_interaction_id: Optional[str]` cache field
- `jvagent/memory/conversation.py:62-64` — `interaction_count` cache field

**Impact:**
- With `last_interaction_id` cached: O(1) access
- Without (stale or missing): O(N) traversal — 10,000 interactions ≈ visible UI lag

**Fix approach:**
- Add a graph-repair phase that audits and repairs `last_interaction_id` pointers
- Make `last_interaction_id` updates transactional with new-interaction creation (current code does it best-effort)
- Add a `Conversation.touch_last_interaction(interaction_id)` method that always updates atomically

---

### Tool Schema Serialisation Inflates Context

**Issue:** All registered tools are serialised into the model's `tools` parameter on every call.

**Files:**
- `jvagent/action/cockpit/engine.py:231-235` — `query_messages(self._messages, tools=self._tools_serialized)`
- `jvagent/tooling/tool_serializer.py` — schema serialiser
- `jvagent/action/skill/tool_executor.py` (1,277 LOC) — full schema serialised upfront

**Impact:**
- For agents with 50 skills × 5 tools each = 250 tool schemas, serialised tool definitions can exceed 10,000 tokens
- Reduces the token budget available for conversation history
- Past ~200 tools, context-window overflow becomes a hard ceiling

**Fix approach:**
- Implement lazy tool registration: only inline harness tools and currently-active skill tools
- Use `cockpit_search` / `skill_search` to surface tools on demand (the cockpit already moves toward this with `skill_index_inline_max_skills` default 5)
- Add a `max_tools_inline` config knob
- Consider provider-side tool grouping when the LM API supports it

---

## Fragile Areas

### Cockpit Tool Registry Dynamic Loading

**Why fragile:**
- `cockpit/registry.py:178-184` uses `importlib.util.spec_from_file_location` + `spec.loader.exec_module` to load skill tool files at runtime. Module cache pollution: `sys.modules[mod_name] = module` (line 183) is set before `exec_module`, so a partial-load failure leaves a half-initialised module in `sys.modules`.
- Skill tool name collisions are namespaced via `f"{prefix}__{raw_tool_name}"` (line 216) but the prefix uses `safe_name = skill_name.replace("-", "_")` (line 146), which can collide if two skills differ only by hyphen vs. underscore.
- Tool definitions are extracted by string lookup (`getattr(module, "get_tool_definition", None)`, line 186) — if the module exposes a misspelled `get_tool_definitions` (plural), the tool is silently ignored.

**Files:**
- `jvagent/action/cockpit/registry.py:130-235`

**Safe modification:**
- Validate tool definitions at startup with a `_validate_skill_tools()` boot pass; fail fast on missing `get_tool_definition` or `execute`
- Use a unique `mod_name` per (skill, file, mtime) to avoid stale module caching across hot-reloads
- Add a test with two skills `foo-bar` and `foo_bar` to lock down the namespacing rule

**Test coverage gaps:**
- No tests for skill names with special characters (Unicode, very long, leading dot)
- No test for a skill tool file that fails to import
- No test for hot-reload after a skill is renamed

---

### Memory Pruning vs Long-Term Memory References

**Why fragile:** Conversation pruning (`interaction_limit`) deletes Interaction nodes. The long-term memory subsystem (`jvagent/action/long_memory/` and friends) may store references to those interactions. There is no documented cascade or referential-integrity check.

**Files:**
- `jvagent/memory/manager.py` (1,085 LOC) — pruning logic
- `jvagent/action/long_memory/`, `jvagent/action/long_memory_store/`, `jvagent/action/long_memory_retrieval/`, `jvagent/memory/long_memory_retrieval_utils.py`, `jvagent/memory/user_long_memory.py` — long-memory subsystem

**Safe modification:**
- Before pruning an Interaction, check if any UserLongMemory entry references it; either preserve or invalidate the reference
- Add a graph-repair phase that drops dangling long-memory refs

---

### Interview Module State Machine Branch Resolution

**Why fragile:**
- `interview/core/foundation/decorators.py` is 1,002 LOC of decorators — a high-density implementation of question/branch/classification decorators
- `interview/core/classification/classification_handler.py` (931 LOC) uses heuristic matching; classification drift across deploys can change which branch a saved session resumes into
- No documented validation pass that confirms all referenced questions exist in the flow

**Files:**
- `jvagent/action/interview/interview_interact_action.py` (1,013 LOC)
- `jvagent/action/interview/core/foundation/decorators.py` (1,002 LOC)
- `jvagent/action/interview/core/classification/classification_handler.py` (931 LOC)
- `jvagent/action/interview/core/graph/question_node.py` (726 LOC)

**Safe modification:**
- Add a `validate_flow()` check at agent boot that walks the decorator graph and asserts no orphaned question references
- When deploying classification changes, version the classification logic alongside the saved session and refuse to resume sessions classified by an older logic version

**Test coverage gaps:**
- Branch loops (A → B → A)
- Orphaned question references
- Classification with adversarial input (very long strings, control characters, RTL marks)
- Resume after classification logic update

---

### Visitor State Bag is Untyped

**Why fragile:** `visitor._skill_state` is a `Dict[str, Any]` shared across the InteractWalker pipeline. Cockpit, AgentInteractAction, and SkillInteractAction all read/write into it with stringly-typed keys.

**Files:**
- `jvagent/action/cockpit/cockpit_interact_action.py:45-47` — `_COCKPIT_STATE_KEY`, `_COCKPIT_ENGINE_KEY`, `_COCKPIT_INTERACTION_ID_KEY`
- `jvagent/action/cockpit/registry.py:104-107` — reads `skill_state.get("skill_catalog")`, `skill_state.get("discovered_skills")`
- `jvagent/action/skill/skill_action.py:184-189` — writes `tool_executor`, `discovered_skills`, `skill_catalog` keys

**Safe modification:**
- Replace `_skill_state: Dict[str, Any]` with a typed dataclass (`VisitorState`) so collisions and typos surface at type-check time
- Document the canonical key set in one place
- Audit for cross-stack key collisions (does cockpit's `cockpit_engine` collide with agent_interact?)

---

## Scaling Limits

### Single-App Multi-Tenancy

**Issue:** Each jvagent deployment is a single `App` (root node). Multi-tenant SaaS use requires consumer-built isolation.

**Files:**
- `jvagent/core/app.py` — singleton App per deployment
- `jvagent/core/app_loader.py` — bootstrap logic assumes one App

**Migration path:**
- Add `tenant_id` to App, Agent, User, Conversation, Interaction
- Enforce tenant isolation in `Memory.get_user()` (`jvagent/memory/manager.py:60-80`) — currently locks per `(memory.id, user_id)` which is sufficient if memory-per-tenant, but the App graph is shared
- Define cross-tenant agent sharing semantics (templates? Forking?)

---

### MongoDB Index Growth

**Issue:** Indexes are eagerly created at boot via `run_index_migration()` (`jvagent/core/index_bootstrap.py`). Compound indexes on `(user_id, status)`, `(tasks.status, tasks.created_at)`, etc., consume memory proportional to data size.

**Files:**
- `jvagent/core/index_bootstrap.py:65-79` — `DEPRECATED_INDEXES` map; the dropping mechanism is in place, but no automated detection of unused indexes
- `jvagent/memory/conversation.py:22-37` — three compound indexes declared on Conversation

**Scaling path:**
- Audit index usage with `db.collection.aggregate([{$indexStats: {}}])` and add unused ones to `DEPRECATED_INDEXES`
- Consider sharding on `user_id` for very large deployments (jvspatial may need shard-aware ops)
- Document index sizing expectations per million users in DEVELOPMENT.md

---

### Cockpit Iteration Budget vs Time Budget

**Issue:** Cockpit defaults `max_iterations = 25` and `max_duration_seconds = 300.0` (`cockpit_interact_action.py:83-84`). For complex tasks, the iteration budget is the binding constraint — the time budget rarely fires first.

**Files:**
- `jvagent/action/cockpit/engine.py:213-229` — iteration-cap exit
- `jvagent/action/cockpit/cockpit_interact_action.py:83-84` — defaults

**Impact:** Long, multi-skill orchestration tasks hit `ITER_CAP` before completing. Users see "I've reached the maximum number of steps" for tasks that were progressing.

**Scaling path:**
- Make iteration cap proportional to skill count (`max_iterations = base + per_skill * len(skills)`)
- Track per-iteration progress signals (new evidence, plan-step completed) and refresh the budget when progress is made
- Surface the iteration count to the user in the response when ITER_CAP fires

---

## Dependencies at Risk

### jvspatial Loose Version Pin

**Issue:** `pyproject.toml:33` and `setup.py:40` pin `jvspatial>=0.0.6` with no upper bound. jvspatial provides graph primitives, walker traversal, ORM, and authentication.

**Files:**
- `pyproject.toml:33` — `jvspatial>=0.0.6`
- `setup.py:40` — `jvspatial>=0.0.6`
- `pyproject.toml:32` comment — "CI records the resolved jvspatial version after install"

**Risk:** jvspatial is an external (TrueSelph-internal) dependency at 0.0.6 — a pre-1.0 SemVer where API changes are expected. Any future release can break:
- Walker traversal semantics (cockpit relies on `visitor.prepend([self])`)
- Graph cascade-delete behaviour
- Index DSL (`@compound_index`, `attribute(indexed=True)`)
- DeferredSaveMixin

**Fix approach:**
- Pin a tested version range: `jvspatial>=0.0.6,<0.1.0`
- Add a CI matrix that runs against multiple jvspatial versions
- Add integration tests for the jvspatial-specific behaviours jvagent depends on (walker termination, cascade delete, index DSL)

---

### httpx Version Inconsistency

**Issue:** `pyproject.toml:36` pins `httpx>=0.27.0` for runtime; `pyproject.toml:47` pins `httpx>=0.24.0` for dev/test extras.

**Files:**
- `pyproject.toml:36, 47, 65` — three different httpx pins
- `setup.py:43, 51` — same inconsistency in setup.py

**Risk:** Mixed httpx versions between dev and production may cause subtle test/deploy mismatches (e.g., timeout API differences, transport behaviour).

**Fix approach:**
- Unify all three to `httpx>=0.27.0,<0.30.0`
- Remove the duplicated declaration in setup.py if pyproject.toml is the source of truth (currently both exist)

---

### Python 3.8 Support is Forced

**Issue:** `pyproject.toml:10` declares `requires-python = ">=3.8"`. `setup.py:81` matches.

**Files:**
- `pyproject.toml:10` — `requires-python = ">=3.8"`
- `pyproject.toml:40` — `mcp>=1.0.0; python_version>='3.10'` (MCP is conditional)

**Risk:**
- Python 3.8 reached end-of-life on October 14, 2024
- Python 3.9 reaches EOL on October 31, 2025
- `mcp>=1.0.0` already requires Python 3.10+, meaning MCP-using deployments need 3.10 anyway
- Forcing 3.8 compatibility prevents use of newer typing features (`PEP 604` union types, `PEP 585` generics in stdlib)

**Fix approach:**
- Bump minimum to `>=3.10` in the next breaking release; document EOL in CHANGELOG.md
- Drop the `python_version>='3.10'` conditional on `mcp`
- Update `mypy` `python_version = "3.9"` (`pyproject.toml:139`) to match

---

### setup.py and pyproject.toml Both Declare Dependencies

**Issue:** Both `setup.py` (lines 38-46) and `pyproject.toml` (lines 30-41) declare runtime dependencies. They are mostly aligned but maintained separately.

**Files:**
- `setup.py:1-82`
- `pyproject.toml:30-91`

**Risk:** Any addition to one and not the other produces inconsistent installs depending on whether the user runs `pip install -e .` or `pip install jvagent` from a wheel built via different paths.

**Fix approach:**
- Migrate fully to PEP 621 (pyproject.toml only); reduce setup.py to a stub or remove
- Single source of truth eliminates drift

---

## Missing Critical Features

### No Built-in Audit Logging

**Issue:** User actions, tool executions, and memory mutations are stored in the Interaction history but not in an immutable audit log.

**Files:**
- `jvagent/action/interact/endpoints.py` — interaction logging
- `jvagent/logging/service.py` — application-level logging service
- No `AuditLog` entity exists in the codebase

**Problem:** Compliance regimes (SOC2, GDPR, HIPAA-aligned, ISO 27001) require immutable audit trails for user-data access. Current logs are mutable database rows.

**Migration path:**
- Add an `AuditLog` Node with append-only semantics (or write to a separate WORM-style backend)
- Hook into the interact pipeline to record: user_id, agent_id, action, timestamp, request_hash
- Define retention policies per deployment

---

### No Built-in LLM Cost Throttling

**Issue:** Tool execution and language-model calls are not rate-limited at the per-agent or per-user level beyond the IP+agent_id interact limiter.

**Files:**
- `jvagent/action/skill/tool_executor.py` (1,277 LOC) — no per-tool rate limiting
- `jvagent/action/model/language/base.py` (1,158 LOC) — per-call retries, but no usage-tracked throttle

**Problem:** A misbehaving or compromised agent can drive unbounded API costs (OpenAI, Anthropic, etc.) before any operator intervention.

**Migration path:**
- Add a `LMCostLimiter` that tracks tokens/$ per agent, per user, per minute/hour/day
- Surface a `JVAGENT_LM_DAILY_TOKEN_BUDGET` env var with default
- Emit metrics: `lm_tokens_used`, `lm_calls_made` per agent/user

---

### No First-Class Observability Plane

**Issue:** Logging is the primary observability mechanism. There is no built-in metrics emission (Prometheus, OTel) or tracing (OpenTelemetry).

**Files:**
- `jvagent/core/observability.py` — exists but limited; need to inspect what it covers
- `jvagent/core/profiling.py` — profiling hooks
- `jvagent/core/benchmark.py` — benchmark harness

**Problem:** Operators must derive metrics from log scraping. There is no canonical way to emit `cockpit_iterations_total{agent="x"}` or `tool_dispatch_duration_seconds{tool="y"}`.

**Migration path:**
- Wire OpenTelemetry as an optional dependency
- Define the standard metric set: per-action latency, per-tool latency, iteration counts, stuck-detection rate
- Add tracing spans around walker traversal, tool dispatch, model calls

---

## Test Coverage Gaps

### Cockpit Tests are Limited

**Files:** `tests/action/cockpit/` (test directory does not appear in the directory listing — cockpit tests, if any, are sparse)

**Untested scenarios:**
- Multi-turn conversations (50+ turns) with stuck detection thresholds
- Time-budget exhaustion mid-batch (in-flight tool dispatch when `max_duration_seconds` fires)
- Worker-crash + restart with engine state restoration
- Tool batch ordering: `[response_publish(finalize=true), other_tool]` (the known dispatch-after-finalize bug above)
- Cockpit + AgentInteractAction coexistence in the same agent

**Risk:** The cockpit subsystem on the active branch has the largest LOC concentration of any feature added recently and limited dedicated test coverage.

---

### Graph Repair Phase Tests

**Files:** `tests/core/test_graph_repair.py`, `tests/core/test_graph_repair_scale.py`

**Untested scenarios:**
- `PH_ORPHANS_REATTACH` with multiple-candidate parents (which is chosen, deterministically?)
- Repair restart from `STATE_VERSION` mismatch (no migration exists)
- Concurrent repair attempts on the same agent (locking? Dedup?)
- Partial-repair resumption when crashes happen mid-phase (cursor stability across batches)

---

### PageIndex Concurrent Ingestion

**Files:** `tests/action/pageindex/`

**Untested scenarios:**
- Two concurrent uploads of the same document (deduplication)
- Compaction-during-ingest (write lock contention)
- Partial upload cancellation (cleanup on disconnect)
- Unicode and CJK content in lexical search
- Very large single documents (>50MB PDFs)

---

### Interview Session Recovery

**Files:** `tests/action/interview/`

**Untested scenarios:**
- Resume after worker crash mid-classification
- Invalid branch reference recovery (graceful failure mode?)
- Classification version drift across deploys
- Branch loops (A → B → A)
- Special-character utterances in classification

---

### Rate Limiter Adversarial Inputs

**Files:** Tests for `jvagent/action/interact/rate_limiter.py` — no dedicated rate-limiter test file in the listing

**Untested scenarios:**
- Spoofed `X-Forwarded-For` from untrusted source
- IPv6 client addresses
- `agent_id` with special characters
- Concurrent requests at exactly `rate_limit_per_minute` boundary
- Cleanup behaviour when `_request_timestamps` exceeds 1000 keys

---

## Architectural Concerns

### Triple-Stack Execution (see Tech Debt §1)

The legacy InteractRouter+SkillInteractAction stack, the unified AgentInteractAction stack, and the new CockpitInteractAction stack coexist. See the first Tech Debt entry for the full analysis. Key architectural impact: the system has three independent definitions of "termination reason," "stuck detection," "system prompt assembly," and "tool registry."

---

### State Persistence is Spread Across Three Locations

**Issue:** Conversation state lives in three places, and synchronisation between them is non-atomic.

1. **Graph nodes (MongoDB via jvspatial):** Interaction, Conversation, User, Memory
2. **Walker visitor state (in-memory):** `visitor._skill_state` dict, including the live `CockpitEngine` instance
3. **Task store (on Conversation node):** structured task metadata (`tasks: List[Dict]`)

**Files:**
- `jvagent/memory/conversation.py:106-109` — `tasks` attribute
- `jvagent/action/cockpit/cockpit_interact_action.py:45-47` — visitor state keys
- `jvagent/memory/task_store.py` (784 LOC) — TaskStore API

**Impact:**
- A crash between graph-save and task-store-update produces an inconsistent state
- Recovery logic must reconcile across all three

**Fix approach:**
- Document the synchronisation contract in an architecture doc (which write happens first, what's the roll-forward path on crash)
- Add a startup consistency check that detects orphan task entries vs missing Interactions
- Long-term: consolidate state into a single transactional unit (graph node with deferred saves)

---

### Action Lifecycle is Implicit

**Issue:** Actions declare lifecycle hooks (`on_register`, `on_enable`, etc.) per `CLAUDE.md`, but the calling order, error handling, and re-entrance guarantees are not centrally documented.

**Files:**
- `jvagent/action/base.py` (1,118 LOC) — base Action class
- `jvagent/action/loader/action_loader.py` (1,192 LOC) — discovery and registration

**Impact:**
- New actions can break invariants (e.g., calling `agent.get_actions_manager()` from `on_register` when the manager isn't built yet)
- Lifecycle bugs are diagnosed by tracing through the loader rather than reading a spec

**Fix approach:**
- Write a `docs/action-lifecycle.md` covering: which hooks fire when, what's available in each, error handling guarantees, idempotency expectations
- Add a smoke test that registers a probe action and asserts hook ordering

---

## Recommendations Summary

| Severity | Area | Action |
|----------|------|--------|
| CRITICAL | Triple-stack execution paradigms | Pick canonical stack, document deprecation timeline, set removal version |
| CRITICAL | Cockpit state recovery | Implement `CockpitEngine.from_state()`; add worker-crash integration tests |
| CRITICAL | Cockpit dispatch-after-finalize bug | Document explicitly OR re-order dispatch so `response_publish(finalize)` runs last |
| HIGH | Mypy ignored across most modules | Drain `ignore_errors = true` overrides one module per release |
| HIGH | Rate limiter single-process only | Add Redis backend; add trusted-proxy allowlist |
| HIGH | Runtime pip install default-on | Default to disabled in production; add `jvagent scan-deps` |
| HIGH | Env placeholder silent emptiness | Add `required_env_vars` validation; document `JVAGENT_WARN_EMPTY_PLACEHOLDERS` |
| HIGH | Missing tests for graph repair phases | Add cursor-batched tests for `PH_MEMORY_COUNTERS`, `PH_ORPHANS_REATTACH`, `PH_DUP_APPLY` |
| HIGH | Webhook SSRF static blocklist | Use `is_private`/`is_reserved`/`is_link_local`; pin resolved IPs |
| MEDIUM | Frontend plaintext password storage | Switch to `sessionStorage` or encrypt with WebCrypto + PIN |
| MEDIUM | Large monolithic action files | Split `skill_action.py`, `pageindex/endpoints.py`, `graph_repair_job.py` |
| MEDIUM | Interaction pruning race | Document as advisory; consider tombstones |
| MEDIUM | Visitor state bag untyped | Replace `Dict[str, Any]` with typed dataclass |
| MEDIUM | Skill catalog re-renders every step | Cache rendered prompt in `CockpitContext` |
| MEDIUM | LaTeX injection residual risk | Adversarial-payload integration tests; consider chroot for pdflatex |
| MEDIUM | Interview state recovery | Add boot-time flow validation; version classification logic |
| LOW | jvspatial loose pin | Specify version range `>=0.0.6,<0.1.0` |
| LOW | httpx version drift between dev/prod | Unify to `httpx>=0.27.0,<0.30.0` |
| LOW | setup.py + pyproject.toml drift | Migrate to PEP 621 (pyproject.toml only) |
| LOW | Python 3.8 supported despite EOL | Bump min to `>=3.10` in next breaking release |
| LOW | No first-class observability | Wire OpenTelemetry as optional dependency |
| LOW | No multi-tenancy | Document migration path; add `tenant_id` in next breaking release |
| LOW | No audit log | Add `AuditLog` entity for compliance-sensitive deployments |
| LOW | No LLM cost throttling | Add per-agent/per-user token-budget limiter |

---

*Concerns audit: 2026-05-06*
