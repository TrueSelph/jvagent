# Audit Index — jvagent

> Aggregated headline findings + recommended remediation order. Date: 2026-05-17.
> Read-only audit. No source files were modified.

Source reports:
- [`AUDIT-core.md`](AUDIT-core.md) — 227 lines
- [`AUDIT-memory.md`](AUDIT-memory.md) — 600 lines
- [`AUDIT-interact-cockpit.md`](AUDIT-interact-cockpit.md) — 976 lines
- [`AUDIT-actions.md`](AUDIT-actions.md) — 633 lines

---

## 1. Totals

| Subsystem | CRIT | HIGH | MED | LOW | Notes |
|---|---:|---:|---:|---:|---|
| core | 5 | 11 | 10 | 6 | SPEC drift in §6.3, §11.7, App.now() timezone |
| memory | 4 | 12 | 12 | 8 | Method-name drift (`append_*` vs `add_*`) across SPEC + ADR |
| interact + cockpit | 5 | 12 | 23 | 18 | 10 SPEC drift items |
| actions library | 6 | 17 | 15 | 9 | 41 packages reviewed; ~25 contract-compliance issues |
| **Totals** | **20** | **52** | **60** | **41** | **173 findings** |

---

## 2. Critical findings (must fix or accept-with-justification)

### 2.1 Security (auth / authz / secrets)

| ID | Where | Issue |
|---|---|---|
| memory CRIT-01 | `memory/endpoints.py:432-463` | `get_my_memory` accepts `user_id` query param + only `auth=True` → cross-user PII leak |
| memory CRIT-03 | `memory/manager.py:565-619` | `purge_conversations(conversation_id=...)` no ownership check → cross-tenant destruction |
| actions XC-1 | `google/*`, `microsoft/*` | OAuth tokens (access + refresh + client_secret) at rest in plaintext |
| actions XC-2 | `google/*`, `microsoft/*` | OAuth `state` carries PKCE `code_verifier` — defeats PKCE |
| actions XC-12 | `postiz_action` | `find_one({"context.enabled": True})` without `agent_id` scope → cross-tenant retrieval |
| core C-4 | `core/callback.py:26-101` | Webhook callback DNS validation / retry: SSRF holes |

### 2.2 Correctness / data loss

| ID | Where | Issue |
|---|---|---|
| core C-1, C-2 | `app.py:88-174`, `app_loader.py:319,348` | `_cached_app` writes outside lock; cached App can outlive its DB context |
| core C-3 | `app.py:537-540` | `object.__setattr__` on `update_mode` may bypass jvspatial dirty tracking → reset silently no-ops |
| core C-5 | `agents.py:64-97` via `/status?sync=true` | Unguarded read-mutate-write overwrites concurrent install/delete deltas |
| memory CRIT-02 | `services/long_memory_service.py:29-30` | `resolve_collection(suffix=)` kwarg not accepted by helper → dormant TypeError |
| memory CRIT-04 | `lock_manager.py:84-85` | Module-level locks not per-event-loop → `RuntimeError: bound to a different loop` on serverless warm start |
| interact CRIT (walker) | `interact_walker.py:678` | `logger.error(..., details=...)` raises TypeError, masks original exception, breaks continue-on-error contract |
| interact CRIT (cockpit revisit) | cockpit revisit path | Stale engine on revisit silently drops user response |
| interact CRIT (curate path) | `cockpit/delivery/delegation.py` | `curate_walk_path` silently drops routed nested IAs not in walker queue |
| interact CRIT (queue full) | `visitor.append` for finalize | At queue full → no final response delivered |
| interact CRIT (tool error leak) | harness tools | `f"Error: {exc}"` bypasses `sanitize_tool_errors`, leaks stack traces to user |

### 2.3 Operational / blast radius

| ID | Where | Issue |
|---|---|---|
| actions XC-3 | `video_generation`, `web_search/serper`, `web_search/serpapi`, `tts_action/elevenlabs`, `google/*` | Sync HTTP libs called from `async def` → blocks event loop |
| actions video_generation/heygen.py | multiple | Claimed webhook secret verification is a no-op log line |
| actions XC-6 | ~25 packages | Missing `endpoints.py` or `from . import endpoints` in `__init__.py` → silent endpoint registration failure |

---

## 3. SPEC-side drift (docs must be reconciled with code)

These findings flag places where the docs I just wrote disagree with the code. Each must be resolved by either fixing the code OR amending the doc.

### From memory audit
- **Method name**: SPEC §5.2, `memory-and-pruning.md`, `adr/0003`, `memory/CLAUDE.md` all reference `Conversation.append_interaction()`. Audit claims actual name is `add_interaction()`. Verify by reading `memory/conversation.py:250`. If audit is right, rename in docs (5 files).
- **Prune rewire order**: ADR 0003 says "disconnect Conv→current, connect Conv→next, delete current". Audit claims actual order is safer/different. Reconcile.

### From core audit
- **SPEC §6.3** "merge mode": doc says "non-destructive merge from YAML; new keys added; existing graph nodes preserved". Audit (`app_loader.py:308-317`) says `--merge` actually only merges 2 keys. Either expand merge logic or amend doc.
- **SPEC §11.7** invariant "App.update_mode resets to run after successful sync": audit C-3 says `object.__setattr__` may bypass dirty tracking — reset may be a silent no-op. Reconcile.
- **`App.now()`**: returns naïve local time by default but `app_now_aware_utc` ([`app.py:518`](../../jvagent/core/app.py)) treats it as UTC. Doc needs to clarify.

### From interact-cockpit audit
10 drift items — most around default values in `cockpit_interact_action.py`:
- `stuck_detection_window` doc says 4, code says 3 (or vice versa) — re-verify
- `block_raw_tool_invocation` is prompt-only, does not actually block
- SPEC references `walker.user`; code has `walker.user_id`
- Other knob-default mismatches between local guide and source

### From actions audit
- **Actions catalog** vs filesystem: 19/39 packages have `info.yaml` `package.name` ≠ directory path. Either rename packages or amend `actions-catalog.md`.
- **Contract compliance section in `action-authoring.md`**: spec says 4-file structure required; audit XC-6 says ~25 packages don't have it. Catalog must reflect actual state, not aspirational state.

---

## 4. Recommended remediation order

Three waves. Each wave should land before the next starts.

### Wave A — Stop the bleeding (this week)

**Goal**: close authz/security gaps + obvious data-loss paths. Small per-change, high blast-radius if missed.

1. memory CRIT-01: add ownership check to `get_my_memory` (`memory/endpoints.py:432`).
2. memory CRIT-03: add ownership check to `purge_conversations`.
3. actions XC-1, XC-2: encrypt OAuth tokens at rest; fix OAuth state to be a real CSRF token, move `code_verifier` to server-side session.
4. actions XC-12: scope Postiz lookup by `agent_id`.
5. core C-4: tighten webhook callback DNS validation; document SSRF posture explicitly.
6. interact CRIT (tool error leak): route harness-tool error strings through `sanitize_tool_errors`.
7. interact CRIT (logger.error TypeError): replace `details=` kwarg with proper `extra=` dict.

**Estimate**: 7 fixes, 2–4 days for a single developer.

### Wave B — Correctness + contract (next 1–2 weeks)

**Goal**: stop subtle drift and silent failures.

8. core C-1, C-2, C-3: fix `_cached_app` lock scope + audit `protected=True` mutation paths (use the `set_app_update_mode` pattern everywhere).
9. core C-5: serialize `Agents.sync_counters` (compare-and-set or distributed lock).
10. memory CRIT-02: fix `resolve_collection` signature.
11. memory CRIT-04: make `_user_lock_manager._locks` per-event-loop using the `app.py:94-117` pattern.
12. memory HIGH-01/02: route reconnect-on-create through `Memory.get_user()` (use lock + compound index, not `find_one`).
13. memory HIGH-03..12: pruning miscount, lock eviction, polling, race fixes (cluster — likely one PR).
14. interact CRIT (revisit / curate / queue-full): fix the silent-drop paths.
15. interact HIGH (12 items): especially `skill_search` `.items()` bug, router cache key, per-interaction iteration cap separate from engine instance, always-execute IAs skipping access filtering.
16. actions XC-3: convert sync HTTP libs to async (`httpx`, `aiohttp`); add `asyncio.to_thread` wrappers where conversion is hard.
17. actions XC-4: move misplaced endpoints under `/actions/{action_id}/`.
18. actions XC-5: rename `info.yaml` `package.name` mismatches OR rename dirs to match.
19. actions XC-6: add missing `endpoints.py` and `from . import endpoints` imports.
20. actions video_generation: implement real webhook secret verification.

**Estimate**: 13 fixes, 1–2 weeks for a single developer.

### Wave C — Doc reconciliation + hygiene (parallel-safe)

**Goal**: docs match code. Can run in parallel with Wave B since it's docs-only.

21. Reconcile SPEC §5 method name (`append_interaction` vs `add_interaction`). One name across SPEC, ADR 0003, memory-and-pruning.md, memory/CLAUDE.md.
22. Reconcile SPEC §6.3 merge behavior — either expand `app_loader.py:308-317` to cover more keys or amend the SPEC to describe actual behavior.
23. Reconcile SPEC §11.7 update_mode reset wording with code reality.
24. Fix cockpit default-value drift (10 items) between `cockpit/CLAUDE.md`, SPEC, and `cockpit_interact_action.py`.
25. Update `actions-catalog.md` with actual contract-compliance status per package (replace aspirational with measured).
26. Document `App.now()` timezone semantics + `app_now_aware_utc` helper.

**Estimate**: 6 doc PRs, 1 day for someone with the audit reports open.

### Wave D — MEDIUM + LOW (backlog)

The remaining ~100 findings (MEDIUM + LOW) feed a backlog. Many are clustered:

- `HandoffInteractAction` hardcoded contact info (actions M-24) — change to required config.
- `task_store.py:303,323` duplicate `add_event` definitions.
- `mcp/`: `npx`-based MCP servers run arbitrary packages without signature check — add an allowlist or document the risk.
- pageindex DNS-rebinding TOCTOU + `process_document_url` `purge=True` blast-radius.
- task_dispatcher module-level global.
- Dead code, unused imports, doc typos.

Estimate: triage into per-action issues; address as part of normal maintenance.

---

## 5. What this audit did NOT cover

- The 12 existing `docs/*.md` files (assumed authoritative for user-facing prose).
- `jvchat/` (reference UI; out of scope).
- `jvspatial` itself — only the boundary; jvspatial owns its internals.
- Active runtime testing — this was static review. Some "would fail under load" findings need load tests to confirm.
- Existing test suite quality — only test *coverage gaps* on load-bearing paths.
- Threat modeling beyond OWASP-top-10 patterns; no dedicated security audit (e.g., crypto, supply chain).
- The cockpit's prompt content (`prompts.py`) — language quality / model performance is not a static-review target.

---

## 6. Confidence and caveats

- Audit was static review by four independent agents. Some findings (e.g., "race condition") are reasoned about, not reproduced.
- One auditor cited specific behavior in `app_loader.py:308-317` and `agents.py:64-97`; spot-check before applying broad fixes.
- The memory audit's "method name is `add_interaction` not `append_interaction`" claim contradicts a direct read of `memory/conversation.py:250` done during doc-writing — VERIFY before mass-renaming. The audit may be reading a different file or path.
- Severity ratings reflect the auditor's judgment; the team may re-rank during triage.

---

## 7. Next actions

1. **Read the four detail reports** before making fixes — context matters for triage.
2. **Decide on Wave A scope** — these are the 7 fixes that I'd land first.
3. **Run a load test** after Wave B to confirm race fixes hold.
4. **After Wave B + C, run a second audit pass** to ensure remediations didn't introduce new drift.

---

## Quick links

- [Core audit](AUDIT-core.md)
- [Memory audit](AUDIT-memory.md)
- [Interact + Cockpit audit](AUDIT-interact-cockpit.md)
- [Actions library audit](AUDIT-actions.md)
- [SPEC the audits checked against](../SPEC.md)
- [Action authoring contract](../action-authoring.md)
- [Actions catalog](../actions-catalog.md)
