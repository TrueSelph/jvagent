# Codebase Concerns

**Analysis Date:** 2026-05-06

## Tech Debt

### Cockpit Module Maturity (New Implementation)

**Issue:** The cockpit module (`jvagent/action/cockpit/`) is a complex rewrite of the interaction pipeline, introduced via recent commits. It's a self-contained subsystem with 24 files (~650 LOC total across multiple modules) that fundamentally restructures how agents process interactions.

**Files:**
- `jvagent/action/cockpit/engine.py` (835 LOC)
- `jvagent/action/cockpit/cockpit_interact_action.py` (526 LOC)
- `jvagent/action/cockpit/registry.py`
- `jvagent/action/cockpit/artifact_tools.py` (485 LOC)
- `jvagent/action/cockpit/memory_tools.py` (750 LOC)

**Impact:** 
- The cockpit introduces a parallel execution model (think-act-observe loop) alongside the existing InteractWalker revisit pattern. This duality increases system complexity.
- State persistence across revisits relies on in-memory `CockpitEngine` instances in `visitor._skill_state`. If the engine crashes or is serialized incorrectly, the loop is orphaned.
- Stuck detection uses two independent heuristics (Jaccard similarity on tool names + primary tool repeat) that may false-positive in legitimate multi-step workflows.

**Fix approach:**
- Add integration tests for cockpit state recovery after simulated crashes
- Document the walker-revisit pattern and when it's triggered vs. abandoned
- Add telemetry for stuck detection threshold tuning
- Consolidate tool registry assembly (currently duplicated across action_resolver, skill_catalog, registry)

---

### Large Monolithic Action Classes

**Issue:** Several action implementations exceed 1000 LOC, making them difficult to test and maintain in isolation.

**Files:**
- `jvagent/action/skill/skill_action.py` (3200 LOC) - Core think-act-observe loop with complex phase management
- `jvagent/action/pageindex/endpoints.py` (2521 LOC) - Document ingestion, indexing, and search combined
- `jvagent/action/skill/tool_executor.py` (1277 LOC) - Tool dispatch, result parsing, error recovery
- `jvagent/core/graph_repair_job.py` (1682 LOC) - 13-phase repair engine with complex state machine
- `jvagent/action/persona/persona_action.py` (1291 LOC) - Response shaping and template merging

**Impact:**
- Cognitive overhead when debugging multi-step workflows
- Difficult to isolate bugs — errors could originate in any phase of a 1600+ LOC file
- Test coverage is often incomplete for edge cases in middle phases (e.g., graph repair's PH_ORPHANS_REATTACH, skill_action's stuck detection, pageindex's document conversion pipeline)

**Fix approach:**
- Extract phase handlers in graph_repair_job into state machine classes (one per phase group)
- Break skill_action into ExecutionPhaseManager, ToolDispatcher, LoopTerminationEvaluator
- Separate PageIndex endpoints into DocumentIngestion (upload, convert) + SearchAPI (query, filter) handlers
- Add test fixtures that exercise each phase independently, not just happy-path integration tests

---

### Missing Tests for Critical Paths

**Issue:** Several high-risk code paths lack direct test coverage.

**Files & Gaps:**
- `jvagent/core/graph_repair_job.py`: Only basic phase transition tests exist. Missing: timeout/resume logic in multi-phase runs, orphan reattachment edge cases, duplicate edge removal under concurrent repair.
- `jvagent/action/skill/skill_action.py`: No tests for stuck detection with semantic tool clustering. Missing: behavior when all tools fail simultaneously, stuck detection false positives on cyclic workflows.
- `jvagent/action/pageindex/core/page_index.py` (1390 LOC): Limited tests for index compaction and rolling-window pruning. Missing: concurrent ingest during compaction, index consistency after crash.
- `jvagent/action/interact/interact_walker.py`: No tests for interaction resolution when session_id is missing or user has no conversations.

**Priority:** HIGH (affects data integrity and user experience)

**Fix approach:**
- Add parameterized tests for graph_repair phases using in-memory MongoDB mock
- Test skill_action stuck detection with synthetic tool sequences (repetitive, semantic duplicates, tool chains)
- Add PostgreSQL/SQLite FTS5 edge case tests for pageindex (special characters, unicode, very large documents)
- Add walker tests for missing/null user/conversation/session states

---

### Rate Limiter State Isolation (Single-Process Only)

**Issue:** The rate limiter (`jvagent/action/interact/rate_limiter.py`) uses in-memory `defaultdict` with `asyncio.Lock()` synchronization. This works only in single-process deployments.

**Files:**
- `jvagent/action/interact/rate_limiter.py:17-45` - `_request_timestamps` dict, single-process scope
- `jvagent/action/interact/endpoints.py:317-387` - Calls to `check_rate_limit()`, `record_request()`

**Impact:**
- Multi-worker deployments (uvicorn with `--workers > 1`) have independent rate limit counters per worker. A client can make `N * rate_limit_per_minute` requests by distributing across workers.
- The `X-Forwarded-For` header trust is a deployment configuration concern but not documented.

**Residual mitigation:** Class docstring recommends Redis for multi-worker deployments (see `rate_limiter.py:18-25`), but no Redis implementation is provided.

**Fix approach:**
- Implement `RedisRateLimiter` subclass as optional dependency
- Add configuration option `JVAGENT_RATE_LIMITER_BACKEND` (memory|redis)
- Document reverse proxy trust in production hardening guide
- Add warning at startup if multi-process mode detected with in-memory limiter

---

### Environment Variable Placeholder Edge Cases

**Issue:** The env placeholder resolver (`jvagent/core/env_resolver.py`) silently converts missing vars to empty strings unless the `${VAR:?}` syntax is used.

**Files:**
- `jvagent/core/env_resolver.py:` `resolve_env_placeholders()`

**Impact:**
- Critical secrets (API keys, database URLs) that fail to resolve silently receive empty strings, causing downstream failures hours later.
- Example: `openai_api_key: ${OPENAI_API_KEY}` without the var set results in an empty key, leading to API call failures instead of startup errors.
- The `${VAR:?}` syntax is opt-in and rarely used in practice.

**Fix approach:**
- Make `${VAR:?}` the default behavior (breaking change — requires migration guide)
- Add a startup validation pass that checks `agent.yaml` for known secret keys and ensures they resolve to non-empty values
- Or: Introduce a `config.require_env_vars` list that triggers validation errors for any placeholder in that list

---

### Dependency Installation at Runtime

**Issue:** Actions declare dependencies in `info.yaml`, and these are installed via `pip install` at runtime if not already present. This is guarded by `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL`, which defaults to `false`.

**Files:**
- `jvagent/core/dependency_installer.py` - Installs from action `info.yaml` dependencies
- `jvagent/cli/server_config.py:343` - Production warning logged (remediation from security review)

**Impact:**
- Supply chain risk: A compromised PyPI package can inject code into a running agent
- Dependency conflicts: Two actions may require incompatible versions of the same library, causing silent fallback to already-installed version
- No signature verification of downloaded packages

**Residual mitigation:** Production warning logged if `JVAGENT_ENVIRONMENT=production` and `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL != true`. Recommendation is to pre-install all dependencies in container images.

**Fix approach:**
- Default `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL=true` when in production mode (behavioral change)
- Add a `jvagent bundle` / `jvagent scan-deps` command that lists all transitive dependencies for pinning in requirements.txt
- Consider package signature verification if using private/trusted PyPI

---

## Known Bugs

### Cockpit Engine Finalization Race

**Issue:** In `jvagent/action/cockpit/engine.py`, tool dispatch happens **before** the `cockpit_finalized` flag is checked. If `response_publish(finalize=true)` is called in the same batch as other tools, those tools execute before the finalize check.

**Files:**
- `jvagent/action/cockpit/engine.py:300-320` - Dispatch happens first, then `if self.state.cockpit_finalized: return`

**Symptoms:** The model may call tools after the user-facing response has already been delivered, causing side effects to occur "out of order" from the UI perspective.

**Trigger:** A tool batch like `[tool_A(), response_publish(finalize=true), tool_B()]` will execute tool_A and tool_B, then check finalized.

**Workaround:** Document that `response_publish(finalize=true)` should be the **last** tool in a batch. Consider enforcing via validation.

**Fix approach:**
- Check `cockpit_finalized` before dispatch, not after
- Or: Document explicitly that finalize is not atomic; side effects may occur after finalization
- Add a comment in cockpit_interact_action explaining the ordering

---

### Graph Repair State Version Mismatch

**Issue:** The graph repair engine (`jvagent/core/graph_repair_job.py`) versioned its state format at `STATE_VERSION = 3`. If the version in a persisted `RepairState` node doesn't match, repair restarts from phase 0.

**Files:**
- `jvagent/core/graph_repair_job.py:25, 132-134` - Version check, mismatch triggers restart

**Symptoms:** A production repair job that had progressed to phase 8 will restart from phase 0 if the code is updated. If the repair has already fixed issues, phase 0 will detect them again, double-repairing.

**Trigger:** Deploy a code change that increments `STATE_VERSION` without a migration path.

**Fix approach:**
- Add a migration function `migrate_repair_state(from_version, to_version, state)` that handles forward/backward compatibility
- Document the version bump procedure in `DEVELOPMENT.md`
- Consider including a timestamp in state so operators can see how long repair has been running

---

### Interaction Pruning Race Condition

**Issue:** Conversation pruning (interaction_limit) occurs in the memory manager, but interactions are also directly queried and mutated via walker endpoints.

**Files:**
- `jvagent/memory/conversation.py:63` - `interaction_limit` field controls pruning
- `jvagent/memory/manager.py:500-600` - Pruning logic (approximate line range)

**Symptoms:** A user may query an interaction via `/api/agents/{id}/memory/interactions/{interaction_id}` and see it, then seconds later it's pruned because the conversation reached its limit. The interaction was "visible, then deleted."

**Trigger:** High-traffic conversation with low `interaction_limit`, multiple concurrent requests.

**Mitigation:** Pruning is lazy (only on `_prune_interactions` calls), not immediate. It occurs at the end of an interaction save, not continuously.

**Fix approach:**
- Document interaction_limit behavior as "advisory, non-atomic" in the API
- Consider adding a "soft delete" flag instead of hard deletion so callers can detect the change
- Or: Make pruning synchronous + transactional within a graph operation

---

## Security Considerations

### Frontend Plaintext Password Storage (Partial Remediation)

**Risk:** jvchat frontend stores plaintext passwords in `localStorage` under `jvchat_saved_credentials_v2` for the "saved accounts" feature.

**Files:**
- `jvchat/src/utils/storage.ts:21-86` - `JVChatStorage.saveCredentials()`
- `jvchat/src/components/Login.tsx` - Credential management UI

**Current mitigation:** UI warnings added (2026-05-02):
- Amber warning banner: "Credentials are stored in this browser's local storage as plain text..."
- Export confirmation notice: "Warning: JSON file contains plaintext passwords..."

**Remaining risk:**
1. XSS vulnerability → all saved credentials exfiltrated
2. Physical access to device → attacker reads browser storage
3. Browser sync (Chrome/Firefox sync) → credentials synced to cloud unencrypted

**Recommendations:**
1. Use `sessionStorage` instead of `localStorage` (clears on browser close)
2. Encrypt at-rest with WebCrypto API + user PIN
3. Document in README: "Do not use saved credentials on shared devices"

**Priority:** MEDIUM (mitigated by UI warnings, but strong recommendation for change)

---

### MCP Filesystem Sandbox Disabled by Default

**Risk:** The MCP filesystem server can be sandboxed to a configurable root, but sandbox mode defaults to `false` (`MCP_FILESYSTEM_SANDBOX_MODE=false`).

**Files:**
- `jvagent/action/mcp/sandbox.py:48` - `sandbox_mode` default
- `jvagent/action/mcp/jvspatial_fs_server.py` - Filesystem server

**Impact:** If MCP tools are exposed to end users, they can read/write arbitrary files on the server.

**Fix approach:**
- Default to `true` when auth is enabled
- Add startup warning if sandbox mode is disabled in authenticated deployments
- Document in production hardening guide

---

### LaTeX Injection Partially Mitigated

**Risk:** User-supplied content is injected into LaTeX templates. Remediation added hex-color validation and `-no-shell-escape` flag.

**Files:**
- `jvagent/skills/pdf_generation/scripts/latex_compiler.py:` (remediated 2026-05-02)

**Residual risk:** LaTeX injection via crafted Markdown headings or list items in the `body` content is theoretically possible. The `_tex_escape()` function handles text segments, but complex LaTeX constructs may bypass it.

**Current mitigation:** `-no-shell-escape` flag prevents the most dangerous vector (shell command execution).

**Fix approach:**
- Add integration tests with adversarial Markdown/LaTeX payloads
- Consider a LaTeX content sandbox (e.g., `pdftex --restricted`)
- Or: Switch to a less expressive templating system (HTML → PDF, Markdown → PDF without LaTeX)

---

### Webhook URL SSRF Validation May Miss IPv6 Edge Cases

**Risk:** SSRF protection validates webhook URLs by resolving the hostname and checking against private IP ranges.

**Files:**
- `jvagent/core/callback.py:85-115` - `_validate_webhook_url()` (remediated 2026-05-02)

**Residual risk:**
- IPv6 link-local addresses (`fe80::/10`) are blocked, but IPv6 ULA (`fc00::/7`) includes some routable ranges. An attacker on the same link may exploit this.
- The blocklist is static. New private ranges (e.g., AWS VPC extensions) are not automatically detected.

**Fix approach:**
- Add tests for IPv6 edge cases (link-local, ULA, loopback)
- Consider using `ipaddress.ip_address().is_private` as the canonical check (Python 3.10+)
- Document the blocklist and how to extend it

---

## Performance Bottlenecks

### PageIndex Lexical Index Compaction Blocks Ingestion

**Issue:** PageIndex compacts the SQLite FTS5 index (`OPTIMIZE` command) during document ingestion when the index exceeds a size threshold. This is a blocking operation.

**Files:**
- `jvagent/action/pageindex/core/page_index.py:600-650` (approximate) - Compaction logic
- `jvagent/action/pageindex/lexical_index.py` - FTS5 interface

**Impact:**
- A large ingestion can trigger compaction, blocking new documents from being indexed until compaction completes
- For very large document sets (1M+ documents), compaction can take seconds or minutes, starving concurrent ingestion requests

**Fix approach:**
- Defer compaction to a background job (async task)
- Implement a configurable compaction threshold and frequency
- Add metrics for compaction duration and index size
- Consider moving from SQLite to PostgreSQL with native FTS for larger deployments

---

### Skill Catalog Rendering Regenerates on Every Request

**Issue:** The skill catalog renders its system prompt section on every `step()` call in the cockpit engine.

**Files:**
- `jvagent/action/cockpit/skill_catalog.py:` `render_system_prompt_section()` 
- `jvagent/action/cockpit/engine.py:150-160` - Called during `initialize()`, possibly cached

**Impact:** For agents with many skills (50+), rendering the full catalog description incurs token overhead per request.

**Residual:** The cockpit spec mentions "Skill index section" caching, but implementation is unclear.

**Fix approach:**
- Cache the rendered skill section in `CockpitContext` after first render
- Add a hash of the skill set to detect when re-rendering is needed
- Consider lazy rendering (only render skills mentioned by the model in earlier turns)

---

### Graph Repair Phase 0 (Memory Counters) Scans All Interactions

**Issue:** The memory counter repair phase iterates over all Interaction nodes to validate conversation counter consistency.

**Files:**
- `jvagent/core/graph_repair_job.py:300-350` (approximate) - PH_MEMORY_COUNTERS phase

**Impact:** For agents with millions of interactions, this phase can take hours and block other queries.

**Residual:** The repair engine is designed to resume incrementally, but this phase is not batched—it either completes or restarts.

**Fix approach:**
- Batch the counter scan into cursor-based pages
- Add a limit parameter: `--repair-max-counter-fixes 1000`
- Consider sampling for large datasets (repair 1% of interactions, extrapolate)

---

## Fragile Areas

### Interview Module State Machine Complexity

**Issue:** The interview module (`jvagent/action/interview/`) implements a complex, multi-phase branching state machine for form-like interactions.

**Files:**
- `jvagent/action/interview/core/classification/classification_handler.py` (931 LOC)
- `jvagent/action/interview/core/foundation/decorators.py` (1002 LOC)
- `jvagent/action/interview/interview_interact_action.py` (1013 LOC)

**Why fragile:**
- Questions, branches, and classifications are interdependent. A missing or invalid question reference breaks the path
- State is persisted to the session, but recovery from crash during branch evaluation is untested
- Classification logic uses heuristic matching; edge cases in input cause unexpected branch paths

**Safe modification:**
- When adding new classification types, add tests for both positive and negative cases
- When adding branches, validate the entire dependency graph (e.g., all referenced questions exist)
- Add consistency checks in `interview_interact_action.py` before resuming a session

**Test coverage gaps:**
- No tests for branch loops (A → B → A)
- No tests for orphaned questions (referenced but not in flow)
- Limited edge case testing for classification with special characters

---

### Cockpit Tool Registry Assembly

**Issue:** Tools are registered across multiple independent functions in `registry.py`, and tool names must match those declared in the cockpit spec.

**Files:**
- `jvagent/action/cockpit/registry.py:50-200` - `assemble_cockpit_tools()` function

**Why fragile:**
- If a tool is renamed in one module but not in another, the cockpit silently ignores the old name
- Action-registered tools are queried dynamically; if an action changes its tool schema, the cockpit doesn't validate
- Skill tools are discovered at runtime; if a skill tool has a circular dependency, it fails silently

**Safe modification:**
- Add a startup validation pass: `_validate_tool_registry()` that ensures all declared tools are registered
- Add a tool schema audit test that compares expected vs. actual tools
- Document the tool naming convention in `cockpit/SPEC.md`

**Test coverage gaps:**
- No test for missing action tools (action exists but tool is not registered)
- No test for circular skill dependencies
- No schema validation test for tool parameters

---

### Memory Pruning and Long-Term Memory Interaction

**Issue:** User memory is pruned separately from interaction history. A user's long-term memory may reference interactions that have been pruned from the conversation.

**Files:**
- `jvagent/memory/manager.py:500-600` - Pruning logic
- `jvagent/action/long_memory/` - User long-term memory implementation

**Why fragile:**
- If `interaction_limit` prunes interactions but long_memory still has references to them, queries for "remind me of that time we..." fail
- No cascade delete logic documented between Interaction and UserLongMemory

**Safe modification:**
- Before pruning interactions, check UserLongMemory for references
- Either: (a) preserve interactions referenced by long_memory, or (b) remove long_memory refs when interactions are pruned
- Add consistency checks at startup

---

## Scaling Limits

### Conversation Interaction Chain O(N) Traversal

**Issue:** Interactions are chained bidirectionally (→ ← edges). To get the last interaction (for pruning), the system must traverse the entire chain from the first interaction.

**Files:**
- `jvagent/memory/conversation.py:87-89` - `last_interaction_id` field (optimization)

**Current capacity:**
- With `last_interaction_id` cache, access is O(1)
- Without it, access is O(N) where N = number of interactions in conversation

**Limit:** If `last_interaction_id` is stale or missing, queries degrade to O(N) traversal. With 10,000 interactions, this is slow.

**Scaling path:**
- Always maintain `last_interaction_id` in a transaction with new interactions
- Add a background job to repair broken `last_interaction_id` pointers
- Consider denormalizing `interaction_count` in the index for fast sorting

---

### MongoDB Index Growth

**Issue:** jvagent creates indexes for every entity class at startup (`run_index_migration`). Index growth is exponential as data grows.

**Files:**
- `jvagent/core/index_bootstrap.py` - Index declaration

**Current capacity:**
- Indexes are created on: `user_id`, `session_id`, `status`, `created_at`, compound indexes on `(user_id, status)`, etc.
- MongoDB allocates memory for each index

**Limit:** With millions of documents, indexes consume significant disk/memory. Compound indexes on high-cardinality fields (session_id, user_id) are expensive to maintain.

**Scaling path:**
- Periodically analyze index usage with `db.collection.aggregate([{$indexStats: {}}])`
- Remove unused indexes (documented in `DEPRECATED_INDEXES`)
- Consider sharding on user_id for very large deployments

---

### SkillAction Tool Executor Token Budget

**Issue:** The skill action engine (`SkillAction.run_to_completion`) builds the full tool registry upfront and serializes all tool schemas for the language model.

**Files:**
- `jvagent/action/skill/skill_action.py:180-200` - `ToolExecutor` initialization
- `jvagent/action/skill/tool_executor.py:100-150` - Schema serialization

**Current capacity:**
- For agents with 50+ skills × 5 tools per skill = 250+ tools, the serialized schema can exceed 10,000 tokens
- Language model context is fixed, so this leaves less room for conversation history

**Limit:** Beyond ~200 skills, context overflow becomes a concern

**Scaling path:**
- Implement lazy tool loading: only serialize skills mentioned in system prompt or discovered via skill_search
- Add a `max_tools_inline` configuration parameter
- Consider tool grouping: "file_operations" → {read, write, list}

---

## Dependencies at Risk

### jvspatial Tight Coupling

**Issue:** jvagent depends on `jvspatial>=0.0.6` (pinned to a minimum version, not a range). jvspatial provides graph primitives, database abstraction, and ORM.

**Files:**
- `setup.py:40` - `jvspatial>=0.0.6`

**Risk:** If jvspatial introduces breaking changes or bugs in a new version, jvagent may silently fail or behave unexpectedly.

**Impact:** Graph corruption, index mismatches, walker traversal loops

**Fix approach:**
- Specify a version range: `jvspatial>=0.0.6,<0.1.0` (or appropriate major version)
- Add integration tests that verify jvspatial behavior (graph consistency, walker termination, index operations)
- Document the jvspatial version dependency in DEVELOPMENT.md

---

### Deprecated httpx Specification

**Issue:** `setup.py` specifies `httpx>=0.27.0` in both `install_requires` and `extras_require['dev']` with different versions.

**Files:**
- `setup.py:39, 50` - `httpx>=0.27.0` and `httpx>=0.24.0` in dev

**Risk:** Mixed versions may cause subtle incompatibilities between dev and production environments.

**Fix approach:**
- Unify httpx version requirement: both install_requires and extras should match
- Pin to a tested version range: `httpx>=0.27.0,<0.30.0`

---

## Missing Critical Features

### No Built-in Multi-Tenancy Support

**Issue:** jvagent has a single "App" (root node) per deployment. Multi-tenant support (multiple orgs/customers) is not implemented.

**Files:**
- `jvagent/core/app.py` - Single app per deployment

**Problem:** Users wanting to run a multi-tenant SaaS on jvagent must build their own tenant isolation layer, which is non-trivial.

**Migration path:**
- Add a `tenant_id` field to App, Agent, and User entities
- Implement tenant isolation checks in the action loader and memory manager
- Consider whether agents can be shared across tenants (licensing, security implications)

---

### No Built-in Audit Logging

**Issue:** User actions (creating conversations, executing tools, modifying memory) are logged to the interaction history but not to an immutable audit log.

**Files:**
- Logging occurs in `jvagent/action/interact/endpoints.py`, but not explicitly as audit events

**Problem:** Compliance (GDPR, SOC2, healthcare) often requires immutable audit trails. Current logging is in the main database and can be modified.

**Migration path:**
- Add an `AuditLog` entity that records user actions immutably
- Configure a separate database or append-only log file for audit events
- Implement retention policies (e.g., 7-year retention for healthcare)

---

### No Built-in Rate Limiting for LLM API Calls

**Issue:** Tool execution and language model calls are not rate-limited. A malicious or misbehaving agent can make unlimited API calls.

**Files:**
- `jvagent/action/skill/tool_executor.py` - No per-tool rate limiting
- `jvagent/action/model/language/base.py` - No API call throttling

**Problem:** Can lead to unexpected charges (OpenAI, etc.) or service degradation.

**Migration path:**
- Add a `ToolRateLimiter` that tracks calls per tool, per agent, per user
- Integrate with the interact rate limiter or create a separate LM rate limiter
- Implement backpressure: return error when limit is exceeded

---

## Test Coverage Gaps

### Cockpit Engine Under Load

**Issue:** The cockpit engine is designed to handle multi-turn interactions, but tests are limited to single-turn or low-iteration scenarios.

**Files:**
- `tests/action/cockpit/` - Test directory

**Untested scenarios:**
- 50+ turn conversations with tool loops
- Time budget exhaustion with in-flight tool calls
- State recovery after a worker crash (engine instance lost)

**Risk:** Multi-turn degradation, memory leaks in long-running sessions

---

### Graph Repair Orphan Detection

**Issue:** The graph repair phase `PH_ORPHANS_REATTACH` is designed to reconnect orphaned nodes, but tests focus on happy-path deduplication.

**Files:**
- `tests/core/test_graph_repair.py`

**Untested scenarios:**
- Orphans with multiple possible parent candidates (which to choose?)
- Orphans created during repair (race condition)
- Partial repair resumption after crash during reattachment

---

### PageIndex Concurrent Ingestion

**Issue:** Document ingestion is not tested under concurrent load (multiple uploads simultaneously).

**Files:**
- `tests/action/pageindex/`

**Untested scenarios:**
- Two uploads of the same document (deduplication behavior)
- Index compaction during concurrent uploads
- Partial upload cancellation (cleanup behavior)

---

### Interview State Recovery

**Issue:** Interview sessions persisted to disk and reloaded are not tested after classification or branch transition.

**Files:**
- `tests/action/interview/`

**Untested scenarios:**
- Resume interview after navigator crash
- Invalid branch reference recovery
- Classification drift (old classification value, new logic)

---

## Architectural Concerns

### Dual Execution Paradigms (InteractWalker + Cockpit)

**Issue:** The system now supports two execution models:
1. **InteractWalker (legacy):** Traverses action graph, executes actions in order
2. **Cockpit (new):** LLM-driven tool selection loop with the walker-revisit pattern

**Files:**
- `jvagent/action/interact/interact_walker.py` - Original pattern
- `jvagent/action/cockpit/cockpit_interact_action.py` - New pattern

**Complexity:**
- Both models coexist. An agent can use PersonaAction (walker-based) or CockpitInteractAction (cockpit-based)
- They have different termination conditions, state management, and error recovery
- Developers must understand both to debug issues

**Recommendation:**
- Document the choice matrix: when to use each
- Consider deprecating InteractWalker in favor of cockpit after stabilization
- Or: Unify both under a common execution abstraction

---

### State Persistence Across Subsystems

**Issue:** State is stored in three places:
1. **Graph nodes** (MongoDB) - Interaction, Conversation, User, etc.
2. **Walker state** (`visitor._skill_state`) - Engine, phase, etc.
3. **Task store** (structured task tracking) - Task metadata

**Impact:**
- Synchronization is non-atomic. If a crash occurs between node save and state update, inconsistency occurs
- Recovery logic must reconcile state across all three stores

**Recommendation:**
- Document state synchronization guarantees in an architecture doc
- Add a startup consistency check that validates state across stores
- Consider consolidating state into a single transactional unit (graph node with deferred saves)

---

## Recommendations Summary

| Severity | Area | Action |
|----------|------|--------|
| CRITICAL | Cockpit state recovery | Add tests for engine crash + resume |
| CRITICAL | Missing test coverage | Add tests for graph repair phases, interview recovery |
| HIGH | Rate limiter multi-process | Add Redis backend option |
| HIGH | Large action classes | Refactor skill_action, pageindex into smaller modules |
| MEDIUM | Cockpit finalization race | Fix tool dispatch ordering or document clearly |
| MEDIUM | Frontend password storage | Switch to sessionStorage or encrypt at-rest |
| MEDIUM | Environment variable defaults | Add startup validation for critical secrets |
| MEDIUM | Interview state machine | Add comprehensive edge case tests |
| LOW | jvspatial version pinning | Specify version range |
| LOW | Deprecated indexes cleanup | Monitor and remove unused indexes |

---

*Concerns audit: 2026-05-06*
