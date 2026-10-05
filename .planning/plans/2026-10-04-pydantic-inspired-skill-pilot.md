# Pydantic-inspired skill architecture pilot

Date: 2026-10-04
Status: Implementation in progress; the opt-in research path and typed
conversational reply are smoke-tested with offline providers and the browser.
Matched evaluation, production effect approval, browser recovery, and the
simplification decision remain open.
Scope: One opt-in, skill-driven execution path; existing research skill and Actions
Decision at completion: Expand, revise, or remove the pilot based on evidence

## 1. Objective

Prove that JV Agent can adopt Pydantic's typed capability composition, explicit
run context, lifecycle boundaries, and validation while preserving its
`SKILL.md` authoring model, Action packages, object-spatial state, native caller
identity, and existing egress contracts. Reduce special cases and maintenance
responsibilities; a second permanent engine is not the intended outcome.

This is an architectural pilot, not a broad provider migration. Compile an
existing skill and its Action operations into a composed execution contract.
Use Pydantic AI's public capability/toolset APIs as the reference implementation
behind an optional pilot path. Do not run its agent loop inside an existing
Orchestrator tick: a turn selects exactly one driver before execution starts.

The existing execution path remains the default during the experiment. Success
does not itself authorize a fleet rollout, legacy removal, or package release.

## 2. Required reading and observed baseline

Read root `AGENTS.md` and every applicable scoped guide before edits. Follow
[SPEC](../SPEC.md), [thin harness](../../docs/thin-harness.md),
[skill authoring](../../jvagent/skills/README.md),
[Action authoring](../reference/action-authoring.md),
[CUCS](../reference/conversation-use-cases.md), and
[deployment limitations](../../docs/HARNESS_DEPLOYMENT.md). Accepted ADRs are
immutable; an implementation decision that changes semantics needs a new ADR.

The source pointers below were checked in the working tree on 2026-10-04.
Line numbers are navigation aids; executing agents must refresh them before
writing implementation claims or bug-fix commit/PR descriptions.

| Existing behavior / seam | Source | Reuse decision |
| --- | --- | --- |
| Research coordinates search/fetch and excludes transactional work | `jvagent/skills/research/SKILL.md:1` | Primary witness; keep its source unchanged |
| Skills already carry dependencies, tools, and task-lock metadata | `jvagent/action/orchestrator/skills.py:52` | Adapt discovered `SkillDoc`; no replacement registry |
| Actions publish decorated or hand-built tools | `jvagent/action/base.py:271` | Preserve `get_tools()` and existing packages |
| Search exports a stable tool name | `jvagent/action/web_search/serper/serper.py:124` | Preserve `web_search__search` |
| Fetch validates URLs and bounds network reads | `jvagent/action/web_fetch/web_fetch_action.py:128`, `:147`, `:224` | Preserve SSRF, redirect revalidation, and body bounds |
| Generic tools invoke raw kwargs | `jvagent/tooling/tool.py:63` | Add server-side argument validation before invoking the guarded binding |
| Skill gate wraps dispatch, not just visibility | `jvagent/action/orchestrator/skill_gate.py:95` | Preserve that distinction in the pilot |
| Action binding rejects authority fields and checks access | `jvagent/action/orchestrator/tools.py:95` | Reuse guard primitives; audit optional ledger handling |
| Task snapshots already persist owner runtime state | `jvagent/memory/task_store.py:464`, `:467`, `:774` | One graph-backed state owner |
| Snapshot helper returns false after some failures | `jvagent/action/orchestrator/skill_tasks.py:632` | Pilot persistence must propagate failure |
| One execute boundary admits a turn | `jvagent/action/orchestrator/orchestrator_interact_action.py:947` | Sole pilot selection seam |
| ReplyAction provides literal publication and optional model shaping | `jvagent/action/reply/reply_action.py:412`, `:425`, `:501` | Use literal publication after validation; admit only supported persona/channel behavior |
| Default harness storage is process-local | `jvagent/harness/runtime.py:107`, `:139` | Do not present it as crash durability |

Previous assessment checks: harness/conformance slices passed; the
orchestrator/wire run had one failure at `tests/wire/test_determinism.py:65`
(29-character user prompt versus a length assertion greater than 100).
Cross-process hash comparisons passed in that run. One external-skill test was
skipped. This is historical assessment evidence, not a current qualification.
Reproduce the baseline; do not weaken a test merely to make the pilot green.

Implementation baseline captured on branch
`codex/pydantic-inspired-skill-pilot` from
`aff7f0a2cbc3a48dab66720e55452db2776545f3`. The branch began with 15 modified
tracked files and five untracked files; several are pre-existing user work and
must remain separately attributable. Pilot changes currently include the
`pydantic-pilot` extra, `uv.lock`, five pilot modules, and pilot tests. The
active checkout uses Python 3.14.3; a fresh Python 3.10.18 environment with
pilot and dev extras resolved `pydantic-ai-slim==2.54.0`, `pydantic==2.13.5`,
and `jvspatial==0.1.1`, and passed `uv pip check`. A separate Python 3.10.18
install without the pilot extra confirmed Pydantic AI is absent while the
legacy Orchestrator and pilot contracts import successfully.

Current implementation smoke evidence (refreshed 2026-10-04): 75 tests passed
in the Python 3.10.18 pilot/Orchestrator compatibility slice, including a
two-turn Agent integration that carries validated evidence through a linked
TaskStore follow-up. All 16 WebFetch tests passed, including the Action's URL
guard coverage. Fresh Python 3.10 environments with and without the optional
extra both pass `uv pip check`; without the extra, Pydantic AI is absent and
the legacy Orchestrator imports. `uv lock --check`, targeted Black/isort, and
`git diff --check` pass. A subsequent broad Python 3.10 suite exposed and fixed
the schema and determinism assertions, along with Python 3.10 test-compatibility
issues; the full suite then passed 4,099 tests with 6 skips. Tracked-file
pre-commit passed; untracked pilot modules separately passed Black, isort,
flake8, and targeted mypy. These results are not browser, crash/restart,
live-provider, or comparative qualification.

Offline performance smoke (2026-10-04): the three-turn CUCS
research/follow-up/changed-Action-version scenario initially passed with 12
deterministic fake model requests, 6 actual Action invocations (search and
fetch on each turn), and 18 conversation saves (6 per turn). Inspection found
two lifecycle writes that could be combined without moving the per-tool evidence
checkpoint: create the pilot task directly as active and write the final
snapshot with its TaskStore terminal transition. Two post-change smoke runs
passed at 100.11 ms (67.29, 16.16, and 16.66 ms per turn) and 86.16 ms (26.23,
14.50, and 45.43 ms per turn), with the same requests and Action calls and 12
saves total per run (4 per turn). Cancellation and synthetic
runtime failure still persisted terminal pilot states without false replies;
storage failure during completion leaves both the in-memory and persisted view
active. Timings exclude provider and public-network latency; model tokens and
production throughput are unavailable. Per-tool evidence remains durable
before it is observed as completed.

Existing uncommitted edits include the Orchestrator, tools, prompts, endpoints,
TaskMonitor, embed paths, tests, and `uv.lock`. Preserve them. Establish an
isolated implementation baseline that includes the intended current behavior;
record its commit and patch digest. Do not silently start from an older clean
revision or stage another person's changes into a pilot commit.

## 3. Pilot witness and explicit boundaries

### Live witness

Use `research` with unchanged `SerperWebSearchAction` and `WebFetchAction`.
Exercise discovery, activation, search, fetch, evidence synthesis, bounded
output validation, clarification, and a later follow-up using stored evidence.
Use an app-local always-active conversation skill for clarification and ordinary
conversation. This preserves the skill-driven authoring model without forcing
every greeting to take an additional discovery round-trip.

The model coordinates the SOP. The server enforces contracts, not a fixed
search/fetch/synthesize script. Validators may reject unsupported source
references or invalid outputs; they must not classify intent or re-extract the
user's message.

### Effects witness

Use a separate test-only skill and Action with one approved mutating operation.
It demonstrates denial, approval, revalidation, idempotency, cancellation, and
uncertain outcomes using a persistent fake service. Never send actual mail,
publish content, mutate customer systems, or add writes to `research`.

The fake service's persistent effect record is the authority for whether the
effect occurred. The task snapshot records the invocation and recovery decision.
The existing in-memory ledger may be a diagnostic projection; it cannot become
a second authoritative effect ledger. Use the same invocation identifier across
the request, approval, fake service, receipt, and replay.

### Exclusions

No new skill specification, registry, workflow DSL, provider fleet, state
database, lease framework, MCP gateway, execution graph, sandbox, StepStore
implementation, production write integration, or mandatory telemetry service.
No migration of InterviewAction, PageIndex, Claude script skills, all channels,
or existing multi-skill task graphs. Existing unsupported semantics must be
reported as unsupported for pilot mode, not approximated silently.
No delegated-agent spawning or nested agent runtime is included in this pilot.
Preserve the Action/tool boundary so a later, separately qualified integration
can expose a specialist agent as an explicit operation without changing skill
authoring or the object-spatial state owner.

Do not implement automatic crash recovery, active-active execution, durable
stream replay, or exactly-once external effects. The pilot proves settled-state
continuation and conservative recovery inspection for a single-writer topology.

## 4. Composition contract and ownership

| Concern | Authoritative owner |
| --- | --- |
| SOP and declared dependencies/tools | Existing skill package |
| Credentials, provider API adaptation, domain operation implementation | Existing Action |
| Validated immutable skill binding | Pilot compiler over `SkillDoc` and enabled Actions |
| Caller, channel, snapshot, invocation IDs, limits | Server-created run context |
| Argument/output validation and composed execution | Pydantic models and the selected pilot driver |
| Authorization, approval, revocation, effect safety | Always-on JV dispatch boundary |
| Persistent skill state | Existing TaskStore task snapshot |
| User-visible response | Existing ReplyAction/ResponseBus path |

Compile once per admitted snapshot, or per run initially. Start without a new
cache. If measurement justifies caching, key it by full native caller, snapshot,
configuration digest, and skill/package digest; never by agent ID alone.

The compiled skill contract contains only stable skill identity/digest, SOP,
resolved Action dependencies, canonical tool bindings/schemas, lifecycle
bindings, state/output model references, and execution limits. Hook binding uses
existing `extends`/dependency semantics. Do not add a generic hook-registration
language or invent a manifest for the pilot. Put pilot state/output models and
binding configuration in trusted app Python; standard `SKILL.md` stays unchanged.

The skill run context holds trusted `(agent_id, user_id, session_id)`, channel,
snapshot ID, correlation/run ID, cancellation, and invocation services. It is
fresh for each execution. It carries no host business model and no model-settable
authority. Never store per-user mutable state on shared Action instances.

Persist a versioned typed snapshot with skill/configuration digests, status,
question, evidence references, bounded necessary excerpts or artifact references,
pending approval identifier, invocation receipts, and validated output as needed.
Reuse TaskStore status and ownership semantics; avoid a second independent task
state machine. Define exact TaskStore-to-snapshot status mapping in P-01.
Do not store credentials, raw model reasoning, or unbounded provider history.

Research is non-locking: do not depend on `ensure_task_lock_task` to create its
state (`skill_tasks.py:368`). Explicitly create a pilot-owned TaskStore task
before execution, using a dedicated proposed `CAPABILITY_PILOT` task type and
the skill name as owner. Map running and waiting snapshots to existing task
statuses; mark completion only after successful final delivery. A follow-up
creates a new task linked to validated prior evidence rather than reopening a
completed task. Legacy continuation and task drains must ignore this task type.
On rollback, preserve snapshots and park pilot work explicitly; legacy must
neither resume nor silently cancel it. Returning to pilot mode requires fresh
validation and reconciliation of any uncertain effect before resumption.

Output contract for research: question/scope, findings, retrieved source IDs,
limitations, and a user-facing brief. Check references against actual search/fetch
receipts. Schema validity does not establish factual correctness; evaluations
must separately inspect whether retrieved material supports the claims.

Installation hooks (`on_register`, `on_startup`, enable/disable/deregister) remain
Action hooks. Run hooks (activate, before/after operation, error, completion)
belong to the composed execution. Mandatory policy runs even before activation.
Define hook order and cleanup/cancellation behavior; do not promise that existing
hooks map automatically to upstream hooks.

## 5. Agent assignments and execution order

Use one coordinating agent and at most three implementation agents concurrently.
Every assignment must say: **You are not alone in the codebase. Preserve user and
other-agent edits; adapt to them rather than reverting them.** Agents read local
guides, own only assigned files, and return evidence and unresolved limitations.
Parallel work begins only after P-01 interfaces are frozen.

Proposed new production surface: four modules under
`jvagent/action/orchestrator/pilot/`: `contracts.py`, `state.py`, `tools.py`, and
`runtime.py`, plus package initialization. These names are a file-ownership plan,
not a requirement to create empty layers. Combine or omit a module when simpler.

| Agent role | Owned implementation / evidence | Restrictions |
| --- | --- | --- |
| Coordinator / integration | P-00, P-01, P-04, P-06; optional dependency files; Orchestrator selection/config seam; narrow legacy task filters; shared docs | Sole editor of shared integration and dependency files |
| Contracts/state agent | `pilot/contracts.py`, `pilot/state.py`; corresponding tests | No edits to TaskStore internals unless a proven blocker is reassigned |
| Composition/dispatch agent | `pilot/tools.py`; corresponding tests | No changes to search/fetch package behavior or global tooling API |
| Runtime agent | `pilot/runtime.py`; corresponding tests | No edits to legacy loop or public endpoints |
| Qualification agent | P-05 tests, fixtures, example app, CUCS, evidence report | Runs after runtime assembly; no production-source ownership |

```text
P-00 baseline → P-01 contracts + upstream feasibility
                         ├→ P-02 typed state ─────┐
                         └→ P-03 tool composition ├→ P-04 driver + integration
                                                └→ P-05 qualification → P-06 decision
```

The runtime agent may prototype against frozen interfaces during P-02/P-03;
integration remains sequential. Reuse an available agent slot for qualification.
No runtime/dependency mutation from multiple agents at the same time.

## 6. Executable work packages

### P-00 — Establish an honest baseline

- [x] Record branch/base, dirty patch, dependency versions, Python version,
  supported topology, and the baseline test results.
- [x] Read scoped guides for action, interact, memory, core, and tests as needed.
- [ ] Capture the legacy research path with deterministic model/network stubs:
  exact tool names/args, activation, egress count, stored state, and prompt cost.
  The new baseline captures the tool trace, activation, egress, TaskStore state,
  and a prompt-surface character proxy. It does not capture provider-reported
  tokens or cost because the deterministic stub replaces the model adapter;
  close that gap in the matched comparison.
- [x] Investigate the existing determinism failure. Its `USER` length greater
  than 100 conflicted with the newer exact-user-utterance contract; changed the
  non-empty guard and reran cross-process hash checks plus prompt-contract tests.
- [x] Record a clean rollback procedure for the pilot selector and state.

Rollback procedure: remove `skill_runtime: capability_pilot` from app/action
configuration or set it to `legacy`; legacy remains the default. Keep any
`CAPABILITY_PILOT` TaskStore records and snapshots intact as inert history.
Legacy skill lookup and active-flow continuation exclude that task type, and
the registered legacy task runners do not dispatch it. Re-enable the pilot only
after current skill/configuration digests and any unsettled invocation are
validated. To remove the experimental implementation, remove the optional
dependency extra and lock entry, the pilot selector/driver branch, the
`CAPABILITY_PILOT` filter constants, and the pilot package/tests; preserve the
existing user work in this shared dirty checkout when applying that removal.
Do not use a blanket reset or delete persisted tasks as part of rollback.

Deliverable: baseline section in the eventual pilot evidence report, with
commands/results and the exact working-tree provenance. No framework code yet.

### P-01 — Freeze small contracts and prove upstream feasibility

- [ ] Freeze method signatures/data models for compilation, dispatch, state
  load/save, continuation, output validation, and driver invocation.
- [ ] Freeze task creation/status/terminal-delivery semantics, follow-up links,
  approval records, uncertain-effect reconciliation, and rollback exclusion.
  Use existing TaskStore APIs; do not add a second scheduler or approval engine.
- [x] Specify the exact supported frontmatter/hook subset. Fail validation for
  unsupported task locks, prerequisites, chaining, or script execution.
  The pilot admits declarative `jv` SOPs with name/description, allowed tools,
  required Actions, channel gates, always-active, and inert version/license/tags
  metadata. It rejects unknown frontmatter, scripts, lifecycle hooks, non-`jv`
  specs, inheritance, chaining/dispatch, task-flow rules, parameters, and
  output overrides rather than silently dropping those semantics.
- [x] Resolve a mutually compatible released Pydantic AI version using public
  documentation and APIs. Pin the pilot extra to `pydantic-ai-slim==2.54.0`
  (stable v2 API, Python >=3.10). Do not build against moving main.
- [x] Run an isolated no-network API spike: stable-ID deferred capability,
  `FunctionModel` requests capability load, existing-named tool executes only
  after load, and typed output shape validates. The `TestModel(call_tools='all')`
  shortcut does not provide valid capability IDs, so use `FunctionModel` to
  assert the discovery sequence. No provider or nested loop used.
- [x] Prove a literal final-publication route through existing ReplyAction.
  Resolve applicable persona/response instructions before validated generation;
  render channel output deterministically. Reject unsupported model-based
  post-generation shaping at admission rather than bypassing required policy.
- [x] Package dependency as an optional `pydantic-pilot` extra. Use
  `pydantic-ai-slim==2.54.0` without provider extras: adapt the existing JV
  `LanguageModelAction` normalized `ModelAdapter` contract to Pydantic AI's
  public model interface. This retains the app's configured provider Action
  and avoids a second model/provider configuration. Verify fresh install and
  `pip check`, including the repository's Python minimum. Preserve
  `jvspatial==0.1.1` consistently. Legacy imports work without the extra.
- [x] Do not add `pydantic-ai-harness` automatically. Add at most one capability
  only if the spike proves a needed behavior cannot be reused cheaply; record
  its dependency footprint, version policy, and avoided implementation.
  The lock and dependency tree contain no `pydantic-ai-harness`; the pilot uses
  one pinned `pydantic-ai-slim==2.54.0` integration dependency and adds no
  provider SDK or second model configuration. This released package supplies
  the deferred-capability and typed-run APIs demonstrated by the no-network
  spike, so no extra capability package is justified.
- [x] Write a new proposed ADR if the pilot alters normative semantics. ADR-0056
  records the opt-in semantics and explicitly leaves adoption to qualification.
  It does not supersede accepted decisions globally or declare the pilot
  approved by itself.

Exit: named released dependency version, public API feasibility, frozen
interfaces, ownership map, and a runtime-selection decision. If feasibility
fails, stop the dependent work and document a smaller JV-native implementation
of the same contracts. Do not implement both alternatives in parallel.

### P-02 — Typed state over existing object-spatial storage

- [x] Implement immutable contract models and a fresh run context.
- [x] Include model settings, Action tool schemas, and required Action package
  versions in the configuration digest; a version-change smoke starts a new
  task without inheriting the prior evidence.
- [x] Create/load/finish pilot-owned tasks for non-locking research explicitly;
  test waiting, completion, follow-up linkage, delivery failure, and rollback.
  Persist approval payload digest, caller, expiry, and invocation ID. Provide
  TaskStore interfaces for receipt lookup and reconciliation-required state.
  Typed invocation records now persist prepared/started/settled state and a
  bounded result; started-but-unsettled work parks pending reconciliation.
- [x] Wrap `TaskHandle.snapshot` / `set_snapshot` with validation and explicit
  missing-task/storage failure. Check that the real Conversation has a working
  durable flush/save method; never accept a no-op fake as persistence proof.
  Real graph round-trip and subprocess tests verify TaskStore persistence.
- [x] Keep one canonical typed snapshot. Bound evidence/history size; retain
  source provenance through references rather than arbitrary truncation.
  Evidence, invocation count, and persisted tool receipts have explicit caps.
- [x] On rehydrate, verify full caller identity, schema version, skill/config
  digests, and snapshot usability. TaskStore/snapshot lifecycle disagreement is
  rejected. Resume requires the current skill ID explicitly. Unsupported
  schema versions now return an actionable refusal that preserves the old task
  and directs the operator to start a new run; there is no generic migration
  engine. Caller, skill, and digest mismatch plus unsupported-version tests
  cover these checks.
- [ ] Revalidate current dependencies and permissions for parked work. Ordinary
  read-only pilot dispatch checks current access; parked approval/effect resume
  is not wired into the production driver, so current dependency/permission
  revalidation for that work remains unqualified. Approval payload, expiry, and
  caller binding exist in the state adapter tests only.
- [x] Add real graph save/reload tests and separate-process restart tests.
  A fresh interpreter reloads a graph-backed Conversation and validates the
  pilot TaskHandle plus its evidence snapshot. This exposed that TaskStore's
  flush-first path lost direct `Conversation.tasks` mutations because
  jvspatial `flush()` returns early when the entity is not dirty. TaskStore now
  calls `save()` before `flush()`; memory and pilot-state regressions pass.

Exit: typed state survives reload with no cross-caller leakage; storage failure
propagates; stale state cannot authorize work.

### P-03 — Compose skills from unchanged Action operations

- [x] Adapt discovered `SkillDoc` and `Action.get_tools()` to the pilot contract.
  Validate dependencies, disabled Actions, missing operations, duplicate names,
  unsupported schemas, and ambiguous lifecycle owners before execution.
- [x] Preserve canonical tool names, Python signatures, defaults, and both
  decorated/hand-built tool compatibility for the supported schemas. Build
  validation with Pydantic; do not write a second type-to-JSON-Schema converter.
- [x] Reject invalid/extra arguments before effects. Reject model authority
  fields independently of schema validation; preserve typed return semantics.
  Pilot composition also applies JV's shared authority denylist recursively, so
  host execution and effect keys cannot be hidden in nested argument objects.
- [x] Bind only skill-permitted operations. Skill activation never changes
  caller authority. Hiding tools is insufficient: direct guessed invocations,
  search paths, host callbacks, and resumed requests must hit the same gate.
- [x] Revalidate current access/revocation at dispatch. Use existing JV guard
  primitives without inheriting catch-and-continue ledger behavior. Fail closed
  on a required snapshot, policy, or invocation-service failure.
- [ ] Implement approval suspension/resumption and invocation receipt handling
  through those interfaces. Bind approval to the exact payload and current
  caller; expired or changed requests require a new decision. Reuse a settled
  receipt only after validation; unknown effect outcomes require reconciliation.
  Composition now fails closed for tools carrying an idempotency/effect
  classification unless an explicit host effect invoker is supplied; the live
  research pilot supplies none. A test-only SQLite fake exercises the same
  composed dispatch wrapper for payload/caller-bound approval, expiry, access
  revocation, duplicate delivery, and conservative handling of uncertain
  pending outcomes. This is not a production approval engine or TaskStore-backed
  recovery implementation; those remain required.
- [x] Distinguish `ToolResult.is_error` from success at pilot dispatch; failed
  results are not returned to the model or evidence observer. Search still
  collapses provider failure and no-results (`serper.py:117`), so the pilot
  must not claim authoritative absence from an empty search.
- [x] Exercise Action exceptions and cancellation end-to-end. Preserve the
  search failure/no-results limitation above; do not overhaul every Action.
  The composed Action boundary propagates raised exceptions/cancellation and
  does not send failed results to the model or evidence observer; the integrated
  CUCS probe cancels while the real composed Search Action is blocked, confirms
  cancellation reaches that operation, persists cancelled task/snapshot status,
  and emits no reply. Synthetic runtime-failure persistence is also verified.
- [x] Prove existing WebFetch SSRF/redirect/body-limit checks still execute.
  The full suite includes the existing WebFetch SSRF, redirect, and response
  size regression tests; the CUCS smoke invokes its guarded fetch path offline.

Exit: the two existing Action packages execute unchanged, with argument
validation and guarded invocation added at the composition boundary.

Offline CUCS evidence uses the real decorated Action tool names and schemas.
The search provider response is stubbed; WebFetch runs through its actual URL
validation, pinned request, response streaming, and renderer against a local
mock transport. The independent WebFetch suite covers private-host rejection,
redirect revalidation, and body bounds. No live Serper provider request is
claimed.

### P-04 — Wire exactly one pilot driver

- [x] Add one opt-in agent-local selector at the existing execute boundary;
  proposed name `skill_runtime`, values `legacy` / `capability_pilot`. Legacy
  remains default. Missing optional dependencies yield an explicit config error.
- [x] Keep one model/tool loop per turn. The pilot does not call the legacy
  `_run_loop` and the legacy driver does not activate upstream capabilities.
- [ ] Implement skill discovery/loading, allowed operations, bounded retries,
  deadlines, cancellation cleanup, typed output validation, and settled-state
  continuation through P-01 public interfaces.
- [ ] Wire waiting-for-approval and reconciliation outcomes to persisted state
  and existing interaction/egress surfaces. Add narrowly scoped exclusions in
  legacy continuation/task drains; verify neither driver consumes the other's
  tasks. No automatic replay of uncertain effects.
- [x] Retain the original user utterance and structured host/proactive context.
  The pilot reloads verified host and session context server-side. For an empty
  TaskMonitor utterance it requires a claimed PROACTIVE task resolved from the
  conversation TaskStore; for a user turn it keeps the user's question and adds
  any active research objective separately. The `ProactiveTaskSpec.context` is
  carried separately, bounded to 2,000 characters, and labeled as data rather
  than instructions or verified evidence. Client-supplied `proactive_*` fields
  do not override the graph task or finalize an unresolved task ID. Regressions
  cover a real claimed TaskStore task, spoofed visitor fields, finalization,
  separate context, and preservation of the user question. Capability activation
  is rebuilt from current discovered skills on every run; client history is not
  admitted as model history.
- [ ] Publish the validated brief through existing `ReplyAction.publish` and
  the existing final-emission latch/channel adapters. Apply supported persona
  instructions before generation and deterministic channel rendering afterward.
  `ReplyAction.reply` can invoke another model via `respond` when shaping is
  enabled (`reply_action.py:425`); do not route pilot output through that branch.
  Reject incompatible shaping configurations before the run. Validate the
  actual emitted response, with no duplicate or additional model-composed final.
- [x] Map correlation/invocation IDs to existing diagnostics. No new trace store.
  The TaskStore task records the run correlation ID, and guarded Action dispatch
  logs record correlation, task, skill, model tool-call ID, and tool name without
  arguments or prompt content. Final publication remains on the ReplyAction path;
  offline browser evidence confirms the UI receives only the validated reply,
  while the full history, skill instructions, and internal prompts stay in the
  model request path.
- [x] Integrate the example app with the existing public interact/embed paths;
  no new HTTP API or frontend implementation. The disposable example app was
  exercised through the existing public interact/embed flow in the offline
  browser smoke; reload recovery and parked-task resume remain open.

Exit: opt-in research completes end to end; toggling back to legacy requires no
data migration. Preserve pilot snapshots but leave them inactive on rollback.

### P-05 — Independent qualification and evaluation

- [x] Add CUCS scenarios using `jvagent.use-case/v1`; reuse its loader/schema.
  The research/follow-up smoke loads the CUCS file and asserts actual search and
  fetch calls, published source text, follow-up linkage, and stored evidence.
- [x] Add test-only effect fixtures with a durable fake service and an operation
  key derived from the invocation ID. Test denial, approval, expiry, changed
  payload, changed caller, revoked access, and repeated delivery.
  SQLite-backed effect records and TaskStore-backed approval/receipt tests use
  the same composed Action wrapper; these do not substitute for worker kill and
  restart checks below.
- [x] Kill a separate worker before effect, after effect/before receipt, and
  after settled snapshot. On restart, inspect persisted service/task state:
  replay supported completed receipts; require reconciliation for unknown
  outcomes; never automatically rerun an uncertain write. The subprocess
  qualification uses a test-only SQLite service plus graph-backed TaskStore:
  a prepared invocation has no effect, a completed external write with a
  pending receipt is parked for reconciliation and cannot resume, and a
  settled service/task receipt reloads after worker exit. This is not production
  effect or approval integration.
- [x] Prove cancellation stops pending work and persists an honest status; a
  storage fault must not be turned into success or an ordinary model retry.
  The CUCS integration probe cancels while the real composed Search Action is
  blocked, verifies cancellation reaches it, persists cancelled task/snapshot
  status, and emits no reply. Synthetic runtime failure also persists its
  terminal state. A separate storage-fault test proves task completion is not
  persisted when the required snapshot flush fails. A new end-to-end run injects
  an evidence-checkpoint failure after Action return and proves the pilot makes
  no ordinary model retry, publishes no result, persists failed task/snapshot
  status, and propagates the storage error.
- [x] Run existing Action, tooling, skill-gate, orchestrator, wire, and embed
  regression slices. Add a no-extra-installed legacy smoke check.
  Python 3.10.18 result: 911 passed, 1 skipped; no-extra import and both
  optional/no-extra `uv pip check` runs passed. The deterministic prompt guard
  and Python 3.10 Annotated/Optional schema regression were repaired and tested.
  Full `tests/` suite after installing `.[test,pydantic-pilot]`: 4,099 passed,
  6 skipped, 31 warnings before the process-restart test was added. After the
  TaskStore persistence-order fix and process-restart test, the full suite was
  rerun to 100% with 6 optional-dependency/fixture skips and no failures. The
  earlier nine failures were independently reproduced and fixed in test
  compatibility/patch setup; no pilot failures.
  Latest complete run after the live-smoke refinements: 4,133 passed, 7
  skipped, 31 warnings (Python 3.10.18).
- [x] Run a browser smoke against the bundled messenger and actual public
  interact/embed paths using a disposable copy of the reference example app
  configuration. Profile, session-open, two streamed research turns, and token
  refresh returned HTTP 200; the UI rendered both replies. The second
  graph-backed pilot task links to the first and both snapshots retain evidence.
  Model and Search are deterministic stubs; WebFetch uses MockTransport while
  retaining its real validation and rendering path. Reload recovery, parked-task
  resume, cancel, refusal, and controlled-final browser cases remain open. See
  the evidence report for limits.
- [ ] Run live provider evaluation only with a newly rotated credential and a
  recorded bounded cost budget. One opt-in model-adapter request previously
  succeeded with the then-current `PilotReply` schema (236 tokens, about 1.825
  seconds, estimated US$0.000122). Research outputs use `ResearchBrief`; brief,
  non-factual conversational turns use `ConversationalReply`. A one-scenario browser smoke has now completed through
  Ollama Cloud GLM-5.3 with real WebFetch, evidence validation, TaskStore
  completion, and clickable citations. Search used a deterministic fixture;
  this remains a smoke, not the ten-scenario/five-run evaluation. The separate
  gated adapter smoke still loads the research capability, calls a deterministic
  fixture Action, and validates its source ID; it permits at most five model
  requests, 4,096 total tokens, and US$0.01 estimated cost. A paired
  fixed-source ten-case manifest and blind scoring rubric are prepared at
  `tests/action/orchestrator/pilot/eval/research-cases.yaml`; no live answers
  have been collected or scored against it.
- [ ] Compare legacy/pilot on identical model, settings, sources, and scenarios:
  source support, output validity, Action call count, model calls, input/output
  tokens, p50/p95 latency, failures, and user correction effort.
- [ ] Write a removable-responsibility inventory and production-code/dependency
  delta. Report what this pilot actually replaces in its path, not projected
  whole-repository deletions.

Deliverable: `.planning/reviews/2026-10-04-pydantic-inspired-pilot-evidence.md`
(created by execution, not this plan), with source revision, configuration,
dependency versions, commands, browser evidence, results, limitations, and
rollback verification. Qualification agent reports findings to the coordinator.

### P-06 — Decide expansion or removal

- [ ] Review every acceptance requirement and the bloat budget below.
- [ ] Choose one outcome: expand with a bounded next plan; revise named gaps;
  or remove the optional driver/dependency and retain useful tested contracts.
- [ ] Do not ship two permanent equivalent execution frameworks. An expansion
  plan must name legacy responsibilities to retire, compatibility adapters, and
  evidence needed for retirement. Actual deprecations are later scoped work.
- [ ] Update public docs only for behavior actually implemented and qualified.
  Do not mark the existing Harness Excellence roadmap complete on pilot evidence.

## 7. Acceptance matrix

| ID | Requirement | Required evidence |
| --- | --- | --- |
| PIL-01 | Existing research skill and both Action packages remain compatible | Unchanged source/package digests; YAML boot and graph reload |
| PIL-02 | Skills compose behavior; no domain steering in foundation | Source review; captured activation/dispatch; shared operation fixture |
| PIL-03 | Invalid, extra, authority-bearing args cause zero operation calls | Boundary tests against real binding, not just validator helpers |
| PIL-04 | Activation gates tools; authority stays always-on | Direct-call, activation, deny, revocation, and resumed-call tests |
| PIL-05 | State is typed, graph-backed, and fully caller-scoped | Graph reload; two callers sharing a session string; non-locking task lifecycle and follow-up |
| PIL-06 | Fresh runtime state; stable configuration/package identity | Concurrent independent sessions, rehydrate, digest mismatch tests |
| PIL-07 | Completion cites retrieved evidence; retries/limits are bounded | Typed output tests + scenario evaluation of evidence support |
| PIL-08 | Effects require approved unchanged payload and current authority | Test-only fake service call/effect records |
| PIL-09 | Crash uncertainty is visible; unsafe writes are not replayed | Separate-process kill/restart at all three boundaries |
| PIL-10 | Cancellation and persistence faults do not become success | Fault injection, operation cancellation, durable status reload |
| PIL-11 | Supported persona/formatting and public envelopes retained; one validated final | Admission rejection for unsupported shaping; actual emitted reply support; wire/browser checks for streaming and non-streaming |
| PIL-12 | Legacy works without pilot extra and after rollback | Fresh install, `pip check`, import/interaction; preserved snapshots and mutual task exclusion |
| PIL-13 | Minimal bloat and measured benefit | File/dependency delta, benchmark, removed-responsibility inventory |

## 8. Bloat budget and evaluation thresholds

These are proposed pilot decision thresholds, not claims about today's system.

- Target at most four substantive new production modules and 1,000 net new
  production Python lines. Count all new bridge/integration code; do not hide
  it in fixtures. Tests/example skill text are reported separately. Overrun
  requires a written simplification review before expansion, not blind clipping.
- One selector, one compiled binding model, one run context, one authoritative
  task snapshot, one guarded dispatch path, and one egress path. No new global
  registry, provider switchboard, or base class hierarchy.
- Compile lazily; cache only after a demonstrated bottleneck. Avoid a universal
  app compiler, full standardization project, or wholesale schema migration.
- At least ten declared scenarios; five repeated live runs per scenario when a
  live budget is available. Separate deterministic test statistics from model
  behavior statistics. Report sample size; do not claim statistical certainty.
- All deterministic acceptance checks must pass. Pilot must add no unsupported
  factual claims or unsafe effect attempts relative to baseline. Compare source
  support using independent review; do not equate JSON validity with quality.
- Initial performance target: median total tokens no more than 10% above legacy;
  p95 duration no more than `legacy p95 * 1.10 + 25 ms` under matched conditions.
  Report activation/model-call differences. Exceptions need measured benefits
  and explicit tradeoffs before expansion.
- Demonstrate at least two concrete responsibilities no longer implemented by
  the pilot path, such as decision parsing and generic argument validation code.
  The legacy code remains during the pilot; do not claim net code removal yet.

## 9. Verification, commits, and handoff

Follow the repository's mandatory commit gate. Stage only owned files in the
isolated checkout before checks; include all newly created owned files so hooks
can see them. Never run `git add -A` across unrelated user changes.

```bash
pre-commit run --all-files
pytest tests/action/orchestrator/ tests/tooling/ tests/action/web_fetch/ tests/wire/
```

Also run the new pilot slices, state/recovery slices, and affected embed tests.
Run the full applicable suite before final integration qualification. A hook
that modifies files must be rerun cleanly. Fix red tests before committing;
never bypass hooks. Report source tests, fresh installs, live model evaluation,
browser smoke, crash/restart, and deployment qualification as separate gates.

Each agent handoff includes owned files, contract versions, exact commands and
results, compatibility differences, limitations, and the next dependent task.
The coordinator owns integration, final checks, and the expansion decision
record. Publishing, deployment, and broad deprecation are outside this plan.

## 10. Upstream design references

Use public APIs and recheck documentation against the released version selected
in P-01. Latest documentation can describe APIs absent from an older release.

- [On-demand capabilities](https://pydantic.dev/docs/ai/capabilities/on-demand/):
  bundled activation, stable IDs, history behavior, and always-on enforcement.
- [Custom capabilities](https://pydantic.dev/docs/ai/capabilities/custom/):
  run-scoped instances and composition/lifecycle boundaries.
- [Hooks](https://pydantic.dev/docs/ai/core-concepts/hooks/): ordering, validation,
  execution, error, and cleanup interception.
- [Dependencies](https://pydantic.dev/docs/ai/core-concepts/dependencies/): typed
  server-supplied execution context.
- [Testing](https://pydantic.dev/docs/ai/guides/testing/): TestModel/FunctionModel;
  procedural model tests are not behavioral quality evidence.
- [Step persistence limitations](https://pydantic.dev/docs/ai/harness/step-persistence/):
  checkpoints do not restore all capability state or guarantee safe effect replay.
- [Version policy](https://pydantic.dev/docs/ai/project/version-policy/) and
  [Harness policy](https://github.com/pydantic/pydantic-ai-harness): qualify
  upstream APIs/dependencies deliberately.
