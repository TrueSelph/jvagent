# Pydantic-Inspired JV Agent Pilot — Evidence (In Progress)

**Status: NOT QUALIFIED.** This report records implementation and test evidence
collected on `codex/pydantic-inspired-skill-pilot`. It does not approve a wider
rollout or stable release.

## Source and environment

- Repository: `/Users/eldonmarks/Briefcase/dev/jv/jvagent`
- Pilot implementation baseline: `aff7f0a2cbc3a48dab66720e55452db2776545f3`;
  first committed pilot revision: `564e1f4b`.
- Runtime tested: CPython 3.10.18; `pydantic-ai-slim==2.54.0`, Pydantic
  `2.13.5`, and `jvspatial==0.1.1` in the isolated pilot environment.
- Dependency remains optional under `pydantic-pilot`. `uv pip check` passed in
  both 153-package pilot and 60-package legacy/no-extra environments. The
  no-extra environment does not import Pydantic AI on the legacy path.
- The pilot baseline is committed on `codex/pydantic-inspired-skill-pilot`.
  Counts below describe that pilot work and do not assert clean artifact,
  deployment, or comparative evaluation evidence.

## Implemented path and evidence

ADR-0056 is now recorded as **Proposed**. It documents the opt-in driver
semantics, preserves ADR-0012 and ADR-0054, and makes qualification a prerequisite
for adoption.

A new deterministic baseline test exercises the legacy research loop with stub
model decisions and stub read Actions. It records four model-loop turns, skill
activation (`research`), exact Search and Fetch operation arguments, one egress,
and no TaskStore tasks for this non-locking skill. The prompt-surface proxy
records 3,522, 3,700, 3,920, and 4,063 serialized characters by turn. These are
not provider token counts or cost: the test replaces `_run_model`, so provider
request serialization and usage reporting are bypassed. This provides a
repeatable operational baseline while the live matched comparison remains open.

The pilot is selected only by `skill_runtime: capability_pilot`; `legacy`
remains the default. The experimental driver currently admits only the
`research` skill and its existing Serper search and WebFetch read operations.
It adapts the configured JV model Action to Pydantic AI, validates research
citations against observed Action results, allows typed conversational replies
for non-factual requests, stores typed pilot snapshots in Conversation TaskStore,
and emits through `ReplyAction.publish`.

Browser regression (2026-10-04): the built-in “What can you do?” Messenger
prompt first failed even though Ollama Cloud GLM-5.3 returned ordinary text.
Because the pilot exposed only `ResearchBrief`, Pydantic AI retried output
validation; the next provider response ended at the configured 1,024-token
limit without text or a tool call. The adapter recorded
`JV model returned neither text nor tool calls`, and the user saw a generic
model-failure message. The pilot now exposes `ConversationalReply` alongside
`ResearchBrief`, with instructions to use the former only for non-factual
conversation and preserve Action-backed source validation for research. A
repeat browser turn with the same prompt returned a typed conversational answer
through Messenger. The new output variant passes focused tests; full-suite
verification and matched research-output checks are still required.

The three-turn CUCS smoke passed with real Action schemas and operations.
Search uses a deterministic provider stub; WebFetch uses its actual validation,
pinned-request, streaming, and rendering path over an offline HTTP transport.
The initial runs made 12 fake model requests, 6 Action calls, and 18 Conversation
saves, at 58.27 ms and 55.26 ms total. A 72.21 ms rerun reproduced the same
counts. Inspection showed one redundant write when creating an already-active
pilot task and one when persisting the validated final snapshot separately from
the TaskStore completion transition.

TaskStore now supports active-at-create and snapshot-bearing lifecycle
transitions. The pilot writes the final snapshot and terminal status together;
failed persistence restores the in-memory task view to its previous status and
snapshot. The post-change smoke passed in 100.11 ms total (67.29, 16.16, and
16.66 ms by turn), then passed again in 86.16 ms (26.23, 14.50, and 45.43 ms
by turn). Both runs made the same 12 model requests and 6 Action calls but 12
Conversation saves. The elapsed-time variation is noise at this sample size;
the two-save-per-turn reduction is deterministic. These offline figures exclude
provider and public-network latency, contain no token usage, and are not a
legacy comparison. A focused rerun on the current checkout completed in 60.72 ms
(30.27, 14.68, and 15.77 ms by turn), again with 12 model requests, 6 Action
calls, and 12 Conversation saves. This confirms repeatability of the call and
write counts while further illustrating that elapsed time is too noisy here to
use as a performance claim.

The integration test cancels while the composed Search Action is blocked, then
verifies cancellation reaches the operation, persists the terminal TaskStore
and snapshot status, and publishes no false reply. A synthetic runtime failure
also persists failure without a reply. A separate-process restart test reloads
the graph-backed Conversation and verifies the active task, caller, question,
and evidence snapshot.
Focused dispatch tests prove Action errors and cancellation do not get reported
to the model or recorded as evidence. A storage-fault test confirms a failed
snapshot flush cannot transition a pilot task to completed.
Pilot state now persists prepared, started, and settled invocation records,
including bounded result receipts. Approval parking binds the approval ID,
payload digest, expiry, invocation, and TaskStore caller; resume rejects changed
callers or payloads and expired approval. An unsettled started invocation parks
with reconciliation required and cannot resume. The SQLite fake now exercises
the composed Action path with TaskStore-backed approval and settled receipt
round-tripping.
Rehydration now also cross-checks the typed snapshot lifecycle against the
TaskStore status (running/active, parked-or-waiting/reconciliation/parked, and
terminal statuses). A regression test corrupts that pairing and confirms load
fails closed. The focused pilot directory passed 38 tests after this change;
current dependency and permission revalidation for parked work remain open.
Review found that the parked-task `resume()` API used the snapshot's persisted
skill ID as its own expected value. It now requires the current compiled skill
ID and rejects a changed ID before resuming. A regression test covers that
case; the state, effect, and crash-recovery test subset passes 21 tests. Current
Action dependencies and permissions still require dispatch-time revalidation,
and production parked-task continuation is not wired.

The restart test exposed a TaskStore durability defect: its flush-first path
could lose direct `Conversation.tasks` mutations because jvspatial `flush()`
returns early when a node is not dirty. TaskStore now calls `save()` before
`flush()`. The pilot restart test and the memory/pilot state suites pass with
this correction.

The new in-run storage fault test exposed a second TaskStore edge: a failed
`TaskHandle.set_snapshot()` write left the uncommitted snapshot in the in-memory
task. The method now restores the previous task/snapshot view on any persistence
failure, matching its lifecycle-transition methods. A regression verifies both
the handle and Conversation view roll back; TaskStore and pilot focused suites
pass. The new test was mutation-checked: removing the rollback made it fail on
the stale in-memory snapshot, then the implementation was restored. The
complete `pytest tests/ -q` suite then exited 0 at 100%, with seven
optional-dependency/fixture or live-call skips. Targeted pre-commit and mypy
passed for TaskStore and its regression test.

Separate worker-kill tests now cover three test-service boundaries. Killing
after TaskStore records a prepared invocation leaves no external effect. Killing
after the fake service records a business write but before its receipt leaves
the TaskStore invocation `started`; restart parks the task for reconciliation
and resume is refused. Killing after the service and TaskStore persist the
settled receipt reloads that result. These tests use graph-backed JSON storage
and a test-only SQLite effect service; they do not verify a production effect
integration, approval UI, or real external-system reconciliation.

Verification collected:

- Current-turn smoke: the pilot directory, host-context tests, and deterministic
  legacy baseline passed 50
  tests; one live-model test skipped because its explicit opt-in flag was not
  set. The new legacy baseline then passed independently and emitted the trace
  summarized above.
- Full `tests/` suite completed at 100% with no failures on Python 3.10 after
  the latest atomic TaskStore lifecycle changes; six tests were skipped for
  optional dependencies/fixtures. The full run exited successfully after
  reaching 100%; earlier exact-count runs predate the two new TaskStore tests.
- After adding TaskStore/snapshot lifecycle consistency validation, the complete
  `uv run --python 3.10 --extra test --extra pydantic-pilot pytest tests/ -q`
  suite again reached 100% and exited 0, with six skips for unavailable optional
  dependencies/fixtures.
- After adding the three separate-worker crash-boundary tests, the same full
  Python 3.10 suite again reached 100% and exited 0, with six optional
  dependency/fixture skips. The focused pilot directory passes 41 tests;
  Black, isort, and flake8 pass for the new crash test.
- After requiring the current skill ID for parked-task resume, the full
  `uv run --python 3.10 --extra test --extra pydantic-pilot pytest tests/ -q`
  suite again reached 100% and exited 0, with the same six skips. The focused
  pilot directory remains at 41 passing tests; state/effect/crash tests also
  pass directly (21 tests).
- Pilot, memory, continuation, TaskMonitor, signature schema, wire, and embed
  regression slice completed at 100% with no failures. The latest pilot
  directory run passed 41 tests, including subprocess restart, three separate
  worker-kill effect boundaries, blocked-Action cancellation, and the
  SQLite-backed effect fixture with TaskStore-backed approval binding, expiry,
  revalidation, settled receipt, repeated delivery, and uncertain-outcome
  refusal; it skipped the gated live-model test. The full suite then reached
  100% with no failures; pytest exited
  successfully with six optional-dependency/fixture skips. The full suite
  result predates the three new crash-boundary cases; the pilot directory
  result includes them.
- After narrowing research output to `ResearchBrief` and propagating normalized
  provider token counts into Pydantic AI's run usage, the full `pytest tests/ -q`
  suite passed at 100% with seven skips for unavailable optional dependencies/
  fixtures and the gated live call. A regression proves a provider-reported
  over-budget response raises `UsageLimitExceeded` after one request. The
  focused pilot and host-context tests passed (47 passed, one gated live test
  skipped). `uv pip check` passed for the 153-package pilot environment;
  `uv lock --check`, Black, isort, flake8, targeted mypy, `git diff --check`,
  and pre-commit hooks over tracked and new files also passed. The no-pilot-extra
  environment and legacy import remain covered by the earlier isolated run.
- The skill resolver/compiler boundary now records and rejects unsupported
  frontmatter, bundled Python scripts, lifecycle hooks, non-`jv` specs,
  inheritance, chaining/dispatch, task-flow rules, parameters, and output
  overrides. Only the declarative SOP subset documented in P-01 is admitted.
  The pilot plus skill-channel resolver slice passed; the full suite again
  completed at 100% with no failures and seven optional-dependency/fixture or
  gated-live skips. Black initially requested formatting for three touched
  files; those files were formatted and the focused slice rerun cleanly.
- The discovery adapter now has a regression proving resolver-reported
  unsupported semantics survive conversion into `SkillDoc`. Mutation-checking
  removal of that mapping made the new test fail; the mapping was restored and
  the focused test passes.
- A ten-case fixed-source quality manifest is prepared at
  `tests/action/orchestrator/pilot/eval/research-cases.yaml`. It covers direct
  extraction, multi-source comparison, conflicts, missing evidence, numeric
  precision, source injection, false premises, bounded calculation, temporal
  qualification, and format adherence, with a blind four-dimension rubric and
  paired operational metrics. A structural test confirms ten independent case
  IDs, source references, and rubric dimensions. No model answers have yet been
  generated or scored; this preparation is not live evaluation evidence.
- Run diagnostics now retain a stable map: each pilot TaskStore record carries
  the turn correlation ID, and guarded Action dispatch emits structured debug
  fields for correlation ID, task ID, skill ID, Pydantic tool-call ID, and tool
  name. Arguments and prompt content are omitted. Unit and Orchestrator tests
  verify the mapping; removing the dispatch log made the unit regression fail,
  and the log was restored. No separate trace store was added. After this integration,
  the full `pytest tests/ -q` suite again exited 0 at 100% with seven
  optional-dependency/fixture and gated-live skips. Targeted pre-commit and
  mypy passed for the changed modules.
- An end-to-end persistence-fault injection now fails the research evidence
  checkpoint after a composed Action returns. The Orchestrator propagates the
  storage error, persists failed TaskStore/snapshot status on the recovery
  write, emits no user reply, and makes exactly two model requests (load
  capability and invoke search), with no ordinary model retry. The targeted
  Orchestrator smoke passed. Cancellation and storage-failure qualification in
  P-05 now have direct integration evidence; the broader open live/model,
  browser-recovery, and legacy-comparison gates remain.
- A Chrome DevTools smoke exercised the bundled jvmessenger demo host and iframe
  against a disposable copy of the reference example app configuration. Only
  Orchestrator, Reply, Search, and Fetch were installed, and the pilot selector
  was set only in that copy. Profile, session-open, two streamed interact turns,
  and session refresh all returned HTTP 200. The embedded UI rendered the
  validated cited reply after each turn. The graph-backed Conversation contained
  two completed `CAPABILITY_PILOT` tasks; the follow-up linked to the first and
  both snapshots retained the observed source. The model and Search Action were
  deterministic local stubs; WebFetch kept its production validation and
  rendering path over a MockTransport. Provider credentials were removed from
  the server process. The browser made no model-provider or Search-provider
  calls. The browser console had no errors and reported one iframe sandbox
  warning about the existing `allow-scripts` plus `allow-same-origin` policy.
  Browser reload recovery and parked-task resume remain untested.

## Bloat and replaced responsibilities

The four substantive pilot modules currently contain 1,322 lines: contracts
(158), runtime/model adapter (451), TaskStore adapter (504), and Action
composition (209). The package initializer adds 27, for 1,349 pilot-package
lines. The generic authenticated host-context helper adds another 53 production
lines. The pilot package is 349 lines above the 1,000-line target; package plus
host-context helper totals 1,402 lines before Orchestrator/TaskStore integration.
The current shared working-tree diff shows 438 insertions/65 deletions in the
Orchestrator and 83 insertions/13 deletions in TaskStore. Those files contain
pre-existing uncommitted work, so those figures cannot be attributed entirely
to this pilot. A clean incremental count for shared-file hunks still needs
isolation. No legacy execution responsibility has yet been removed.

The production Orchestrator currently calls the pilot state adapter for
completed-evidence lookup, task creation, evidence saves, and terminal
complete/fail/cancel transitions. Invocation preparation/start/settlement,
approval parking, reconciliation, and resume methods are currently exercised
only by tests; model-triggered effects and a production approval flow are not
wired. These prototype-only state methods are a primary simplification or
removal candidate if the next phase remains read-only.

AST line accounting confirms 245 lines across `record_invocation`,
`mark_invocation_started`, `settle_invocation`, `_update_invocation`, `park`,
`wait_for_approval`, `require_reconciliation`, and `resume` have no production
call sites; they are consumed by pilot state/effect/crash tests. Removing them
would reduce the 1,349-line package to about 1,104 lines, still 104 over budget,
and would abandon the approved PIL-08/PIL-09 contract evidence. Keep this as a
named simplification candidate pending an explicit decision to defer those
contracts; do not delete it as a cosmetic line-count exercise.

The current responsibility delta is narrower than a replacement-harness claim:

| Responsibility | Pilot path evidence | Net status |
| --- | --- | --- |
| Model/tool iteration | Pydantic AI performs the bounded tool loop | Delegated on this path; legacy implementation remains |
| Tool argument validation and typed output parsing | Pydantic AI schemas and output model | Delegated on this path; JV binding/access guards remain |
| Skill discovery, Action ownership, authorization, caller identity | Existing JV compiler inputs and guarded Action dispatch | Still JV-owned |
| Durable task ownership and evidence checkpointing | Existing graph-backed TaskStore through a pilot adapter | Still JV-owned; adapter is new code |
| Approval suspension, effect receipts, reconciliation, parked resume | Test-only TaskStore adapter methods and fixtures | No production responsibility replaced; prime removal candidate while effects stay excluded |
| Final response publication and channel policy | Existing ReplyAction/ResponseBus path | Still JV-owned |

The optional package adds exactly one pinned direct dependency,
`pydantic-ai-slim==2.54.0`, behind `pydantic-pilot`; Pydantic is already a
direct JV runtime dependency. The current pilot package is 1,349 lines (1,322
across four substantive modules plus a 27-line initializer), 349 lines over
its 1,000-line target before counting Orchestrator integration. From the
implementation baseline `aff7f0a2`, the current `jvagent` source diff is
2,497 insertions and 264 deletions (net +2,233 lines). That tree-wide number
includes shared integrations and compatibility changes and must not be
attributed wholly to the pilot without a per-hunk inventory. A removable-code
decision remains required before expansion.

The locked dependency tree contains no `pydantic-ai-harness`. The pinned Slim
package provides the deferred-capability and typed-run APIs demonstrated by
the no-network spike; no provider SDK or second model configuration was added.
The scoped `uv tree --locked --package pydantic-ai-slim --no-dev
--group pydantic-pilot --depth 2` inspection shows its direct and second-level
requirements, including `httpx2`, `pydantic-graph`, and the Pydantic version
already present in JV's runtime.

Delegated agents are not implemented or evaluated by this pilot. Its explicit
composition seam remains a skill plus Action operations, with one selected
driver per turn. A future specialist-agent integration could be exposed as an
explicit Action/tool, but nested runtime lifecycle, context isolation, budgets,
cancellation, and tracing would need separate contracts and qualification.

A bounded live adapter smoke was previously performed through
`OpenAILanguageModelAction` and Pydantic AI using GPT-4.1 mini. That historical
call returned the then-current `PilotReply` output (one request, 236 total
tokens, about 1.825 seconds elapsed, estimated cost US$0.000122); it does not
verify the current stricter research output contract. The gated test was updated
to use the same JV Ollama Action and cloud model as the example app; the latest
execution is recorded below. The earlier OpenAI credential incident remains
unresolved, so no additional OpenAI call was made.

The research driver now compiles only `ResearchBrief` as its output type; the
former generic `PilotReply` alternative was removed so research completion cannot
bypass evidence-reference validation. The JV-to-Pydantic adapter also carries
normalized prompt, completion, and cache token usage forward so Pydantic AI's
configured limits use provider-reported counts rather than estimates when those
counts are available. Regression and full-suite checks cover these changes, but
the stricter output contract still requires a repeat live smoke.

Responsibilities demonstrably delegated to Pydantic AI on this path are typed
research-output validation, tool argument validation, and the bounded agent/tool loop.
JV remains responsible for app configuration and provider selection, skill and
Action discovery, access checks, TaskStore ownership, and user-facing egress.
The bridge also adds message conversion, run-context enforcement, source
validation, and state adaptation; those costs must be counted in any net-benefit
claim.

## In-browser smoke — Ollama Cloud GLM-5.3 (2026-10-04)

JV Messenger completed one fresh, authenticated browser turn against
`glm-5.3:cloud` through `OllamaLanguageModelAction`. The pilot loaded the
app-local script-free `research` skill, called the existing search and fetch
Actions, validated a `ResearchBrief`, persisted a completed `CAPABILITY_PILOT`
TaskStore item, and published the reply through `ReplyAction`. The response
included clickable citations to the fetched Pydantic AI output and tools pages.
The captured interaction reports 6 model calls, 14,448 prompt tokens, 1,558
completion tokens, 16,006 total tokens, and 56.9 seconds aggregate model-call
time. Cost was not reported by this Ollama path.

The first browser attempts exposed three real gaps: built-in `research` was
correctly rejected because it bundles unsupported scripts; `RetryPromptPart`
validation details were not converted to model-readable text; and large Action
results could exceed the 12,000-token default. The pilot now uses Pydantic AI's
`RetryPromptPart.model_response()`, caps each Action result at 4,000 characters,
and defaults to a still-bounded 20,000 total tokens. The browser run then
completed. A regression also fixes an egress gap discovered in that run:
validated source IDs are now rendered as citations from the observed evidence.
The full Python 3.10 suite after these refinements passed: 4,133 passed,
7 skipped, 31 warnings in 133.30 seconds. The focused pilot suite passed
51 tests with one gated live-call skip.

Search results in this smoke were supplied by a deterministic local Serper
fixture, which returns the Pydantic AI documentation regardless of query. The
subsequent WebFetch requests reached the public documentation site and followed
its redirects. This verifies provider-to-Action-to-TaskStore-to-Messenger wiring
for one research scenario; it is not a live search-provider evaluation, a
latency distribution, a matched legacy comparison, or broad provider
qualification. The Messenger remains available locally at
`http://127.0.0.1:3100/sandbox` for hands-on review.

## Open qualification gates

### Task snapshot rehydration

`PilotTaskStore._read_snapshot` now distinguishes unsupported schema versions
from malformed version-1 data and returns a recovery instruction: preserve the
task and start a new pilot run. It does not mutate or migrate the unsupported
task. Existing rehydration checks still bind caller, skill ID/digest,
configuration digest, and TaskStore lifecycle status. The added unsupported
version case passes with the state and crash/restart suites (16 passed); current
dependency/permission revalidation for parked production work remains open.

### Live request stability recheck (2026-10-04)

To avoid changing the user's original local service or its database, the updated
code was started against a disposable copy of the Messenger smoke app and graph
on ports 8001/3101. A one-request test proxy delayed the first call for 16
seconds, then forwarded it to the configured `glm-5.3:cloud` endpoint through
Ollama. A raw SSE client observed `start` at 0.16 seconds, `heartbeat` at 15.31
seconds, then message and final events at 22.83 seconds. The interaction
completed with a validated brief after 5 model calls and 8,317 total tokens.
Search used the existing deterministic local fixture; WebFetch reached the
public documentation site. This verifies the heartbeat on the actual Cloud
model request path with an intentionally delayed transport.

A fresh Messenger browser session then completed the same brief research path
in 3.3 seconds with 4 model calls and 5,806 tokens. It returned a clickable
citation to the official overview page. The browser also visibly rendered the
bounded limit message on the separate over-budget turn above. The successful
browser turn, delayed raw-SSE turn, and over-budget user-visible failure cover
success, a long in-flight response, and a bounded failure on the isolated
current-code service. The focused interaction and pilot suites now pass 190
tests with one opt-in live-model test skipped; targeted pre-commit hooks pass.

The isolated Messenger browser also reproduced total-token exhaustion on a
longer 8-call turn (21,482 tokens, 24.9 seconds). Before the handling fix, this
failure left only the user message in the widget. After the fix, the browser
rendered the explicit limit response and the TaskStore retained a failed task;
the turn did not remain blank or run indefinitely. This is an expected bounded
failure, not a successful research answer. The original service on port 8000
and its smoke database were left running and unchanged.

### Request stability follow-up (2026-10-04)

Inspection of the persistent Messenger smoke graph found two additional turns.
The successful research turn completed in about 60 seconds; one of its six
GLM-5.3 Cloud calls took 50.58 seconds. A later short "What's your name?" turn
closed without a persisted response after its last model call returned
`finish_reason=length` at 1,024 completion tokens and no text/tool call. The
adapter rejects that empty model response and the streaming endpoint emits its
client-safe error event. The latter is a failed turn, rather than evidence of
an HTTP timeout; the slow successful turn is consistent with an idle-stream
timeout at a client or intermediary.

The SSE wait loop now emits an ignored `heartbeat` event every 15 seconds while
retaining 250 ms disconnect checks. Focused interaction and pilot suites passed
(188 passed, 1 skipped), targeted pre-commit hooks passed, and `git diff
--check` passed. The API process used for the browser smoke predates this edit,
so the heartbeat has not yet been verified over a live browser stream. A fresh
browser run against the reloaded server remains an open qualification gate.

### Reloaded-service recovery check (2026-10-04)

After the empty-response recovery change, the isolated API was restarted on
port 8001 and health returned 200. One bounded live Ollama Cloud GLM-5.3
streamed interact request completed over HTTP 200 in about 7.8 seconds, made
four model requests, called the deterministic local search fixture and public
WebFetch, and returned a source citation. This confirms the reloaded provider
and SSE path still completes a live turn. It was an API-level check, not a
browser verification of the new empty-response branch.

The response text was “One-sentence definition of Pydantic AI with official
source citation.” It repeats the requested format instead of explaining the
subject. The typed `ResearchBrief` and valid citation therefore do not establish
answer quality; this is a concrete quality failure to include in the matched
legacy/pilot evaluation, not a successful-answer sample. A separate regression
now verifies that an empty/invalid JV model response persists a failed pilot
task and publishes a bounded retry response through `ReplyAction`. The pilot
and streaming regression slice passed 59 tests with one opt-in test skipped.
The complete Python 3.10 suite after this change passes 4,137 tests with seven
optional/live skips; Black, isort, flake8, and `git diff --check` pass for the
changed Orchestrator and regression test.
The visible browser branch remains unverified: the original tab inspection
timed out, and the second tab is at the Messenger login screen.

The pilot now carries proactive context separately from ordinary user text.
For a scheduler turn with an empty utterance it requires a claimed PROACTIVE
task resolved from the conversation TaskStore; on a user turn it preserves the
user question and adds an active research objective as structured system
context. The typed snapshot records the associated proactive task ID. A
regression creates and claims a real graph-backed task and proves spoofed
`visitor.data` fields do not replace its directive or ID. The TaskMonitor,
pilot, streaming, state, contract, and crash-recovery subset passed 59 tests
(one opt-in live test skipped); the full suite then passed 4,137 tests with
seven skips. This verifies the Orchestrator-to-TaskStore handoff locally, not a
live scheduler tick or the resulting Messenger delivery; those remain open.

- The current `ResearchBrief` path has one successful end-to-end GLM-5.3 Cloud
  browser smoke. The broader provider evaluation and matched legacy comparison
  remain open. The OpenAI credential in the incident below should be revoked
  before any further OpenAI use.
- The before-effect, after-effect/before-receipt, and settled-effect worker-kill
  boundaries are covered by subprocess tests against a test-only SQLite effect
  service and graph-backed TaskStore. These tests establish conservative task
  state behavior for that fixture; they do not qualify a production effect
  integration. The pilot currently admits read-only operations and the
  Orchestrator installs no effect invoker. TaskStore approval/receipt methods
  exist, but model-triggered effects and an operator approval interaction are
  not wired in production. The optional effect-invoker interface fails closed
  when absent.
- Storage-fault injection during a running pilot, including interruption while
  persisting intermediate invocation state, remains open. A narrower test does
  prove that a failed final snapshot flush cannot be reported as successful
  completion.
- Legacy-versus-pilot matched quality, token, latency, failure, and correction
  measurements are absent. The offline CUCS figures are not comparative proof.
- The source/dependency inventory and P-06 revise decision are recorded.
  Selecting `skill_runtime: legacy` now parks active pilot tasks without
  importing the optional pilot package, preserves supported snapshot fields,
  and marks any started-but-unsettled invocation for reconciliation. The legacy
  runner registry refuses the reserved pilot task type. Graph-backed tests
  cover snapshot reload and refusal to resume uncertain work; the legacy
  execute-boundary test verifies parking occurs before `_run_loop`.
  `PilotTaskStore.resume` validates caller/skill/configuration identity, but the
  Orchestrator does not yet re-enter a parked pilot task automatically; that
  production continuation remains open under PIL-04.

Credential handling note: an attempted key entry was interpreted as a shell
command and appeared in the terminal transcript before returning HTTP 401. The
matching shell-history entry was removed without printing the value. Revoke that
credential before further live calls; the transcript may still contain it. No
credential value is stored in this report or the repository. The successful
single adapter call described above used a subsequent hidden-input entry.

### Messenger request-budget retest (2026-10-04)

The user's fresh Messenger session had persisted failures showing the pilot
stopped at Pydantic AI's `request_limit=8` on one research run and
`tool_calls_limit=12` (attempting call 13) on the next. Provider calls were
short; these were harness usage-budget failures rather than transport timeouts.
The Orchestrator now defaults to 16 model requests, 24 tool calls, 30,000 total
tokens, and 6,000 output tokens per run. Each ceiling is configurable on the
Orchestrator Action and included in the run configuration digest. A budget
fallback names the category that stopped the run while the TaskStore retains
the provider/library diagnostic.

After reloading the isolated API on port 8002, an in-browser live GLM-5.3 Cloud
research turn completed through Messenger and the graph-backed TaskStore marked
its pilot task complete. It took about 12.1 seconds, recorded eight model
requests and 14,654 total tokens, and returned a citation. The answer only found
an unrelated Pydantic AI documentation page, so this verifies request delivery,
tool-loop completion, persistence, and egress—not research relevance or factual
quality. The pilot remains NOT QUALIFIED; a relevant-source evaluation and
matched legacy comparison remain open.

### Messenger failure follow-up (2026-10-05)

The saved interaction record explains the generic failure: it took 14.78
seconds, and was not a timeout. The first Ollama `glm-5.3:cloud` model call
completed with text. The second consumed all 1,024 configured completion
tokens, ended with `finish_reason=length`, and returned no text or tool call.
That earlier code version stored only the generic adapter failure reason. The
current branch retains the normalized finish reason and token count, logs the
run and task IDs, and gives output-length and content-filter stops distinct
user-facing explanations. It does not automatically retry the model call.

The Messenger static server on port 3102 was running, but its API on port 8002
had stopped after a later restart attempted the interactive destructive
`--update --source` flow. The personal app has a configured Serper key but no
`JVSPATIAL_JWT_SECRET_KEY`; its persisted action still targets the synthetic
search fixture. The graph database and conversation files were left
untouched; destructive source sync was not applied. For a current-branch browser
smoke, the API was restarted against the same app data in non-destructive mode
with authentication disabled for that loopback-only session. It returned HTTP
200 on `/health`, and “In one sentence, what can you help me with?” received a
complete model-generated answer in about 12 seconds. This confirms a basic live
turn, not live research accuracy or broad latency stability. A timeout
exception with an empty message now records the configured runtime ceiling and
logs its run/task IDs before returning the existing time-limit reply; the
Orchestrator regression verifies persisted failure detail and the correlated
warning.

Legacy locked-flow failure cleanup was also found to cancel every active task
sharing its owner, including a `CAPABILITY_PILOT` task. Cleanup now skips
non-flow task types, and a graph-backed regression verifies the legacy skill is
cancelled while its same-owner pilot task remains active.

### Rollback isolation retest (2026-10-05)

The legacy driver now parks active `CAPABILITY_PILOT` tasks before entering the
legacy loop. The adapter uses generic TaskStore JSON so it does not import the
optional Pydantic AI package. It preserves schema-v1 snapshot fields, marks
`started` invocation records as requiring reconciliation, and leaves terminal
tasks alone. `CAPABILITY_PILOT` is reserved from legacy runner registration and
excluded from the runnable registry. Tests prove the execute boundary parks
before `_run_loop`, same-owner failure cleanup cannot cancel pilot work, and
legacy task drains cannot claim it.

In a fresh Python 3.10 environment installed with `.[test]` and without the
`pydantic-pilot` extra, `uv pip check` passed, `pydantic_ai` was absent, legacy
Orchestrator/continuation imports passed, and the focused task-drain/driver tests
passed (21 tests). With the pilot extra, the focused typed-state and rollback
regressions passed (34 tests); a real JsonDB Conversation reload retained the
typed snapshot and refused resume when an invocation was unsettled. This
qualifies rollback isolation, not Orchestrator-level pilot continuation: the
legacy-selected Orchestrator still does not resume parked work when pilot mode
is re-enabled.

### P-06 coordinator review and decision (2026-10-05)

**Decision: revise the named gaps before expansion.** The optional driver stays
experimental and is not qualified for adoption. The audit below covers all
acceptance IDs in the approved plan's matrix:

| ID | Status | Evidence and remaining limitation |
| --- | --- | --- |
| PIL-01 | PARTIAL | Legacy/no-extra import and Action integration checks pass; a clean package/source digest comparison against the existing research skill and both Action packages is not recorded. |
| PIL-02 | PARTIAL | Skill activation and declared Action dispatch are exercised, and pilot instructions now defer tool selection to the active skill. The production driver still admits only the named research skill and Serper/WebFetch owners. |
| PIL-03 | PASS | Composed-binding tests reject invalid, extra, and authority-bearing arguments before operation calls. |
| PIL-04 | PARTIAL | Activation, current read-tool authorization, denial, and revocation are tested. Production parked-task resume and dispatch-time permission/dependency revalidation are not wired. |
| PIL-05 | PASS | Typed caller-scoped snapshots survive graph and separate-process reload; lifecycle and digest mismatch tests fail closed. |
| PIL-06 | PASS | Fresh run contexts and configuration identity are tested across independent sessions and rehydration. |
| PIL-07 | PARTIAL | Typed output, observed-source citation checks, and bounded limits pass deterministic tests. Live output has included irrelevant sources and a malformed “definition” response; no scored live evaluation exists. |
| PIL-08 | PASS, test-only | The composed test fixture checks approval/payload/caller/expiry binding and duplicate delivery; the production driver admits no effect tools or approval UI. |
| PIL-09 | PASS, test-only | Separate-process fake-service tests cover pre-effect, uncertain post-effect, and settled receipt recovery; no production write integration is claimed. |
| PIL-10 | PASS | Cancellation and persistence-fault tests preserve terminal state and avoid false success or ordinary model retry. |
| PIL-11 | PARTIAL | Offline public Messenger flow verifies the emitted envelope and citations; unsupported channel shaping is rejected. Live answer quality and post-change browser behavior remain open. |
| PIL-12 | PASS | Fresh no-extra install/import/interaction, optional dependency checks, selector rollback, graph-backed snapshot preservation, uncertain-invocation refusal, and mutual task-drain exclusion pass. The legacy execute-boundary regression verifies active pilot work is parked before the legacy loop. |
| PIL-13 | FAIL | The four-module package is 1,349 lines, 349 over its target; the tree delta is net +2,233 lines. Comparative benefit and a removable-code decision are not demonstrated. |

Required next work before an expansion decision: restore the personal API and
configure a real Serper key; run the planned ten-scenario/five-repeat provider
evaluation within a recorded cost budget; complete the matched legacy/pilot
comparison; and identify justified simplifications for the 349-line overrun.
Keep the read-only boundary in force. If effect-capable production behavior is
proposed later, first wire current authority/dependency revalidation, parked
resume, approval, and reconciliation through the existing interaction and
TaskStore surfaces, then repeat crash-boundary qualification. These gaps and
the absent agent/sub-agent evaluation rule out expansion today.

See the implementation checklist and acceptance matrix in
[`../plans/2026-10-04-pydantic-inspired-skill-pilot.md`](../plans/2026-10-04-pydantic-inspired-skill-pilot.md).

### Ollama Cloud live adapter retest (2026-10-05)

The gated adapter smoke now instantiates `OllamaLanguageModelAction`, uses the
signed-in local Ollama daemon to call the cloud model `glm-5.3:cloud`, and
keeps a deterministic fixture Search Action. The live run passed through
Pydantic AI capability loading, tool invocation, and typed `ResearchBrief`
validation: one fixture Action call, three provider calls, 2,017 total tokens,
4.696 seconds elapsed, and an estimated US$0.0037238. The estimate applies
standard, uncached-input rates ($1.40 per million input tokens and $4.40 per
million output tokens) from the [Ollama pricing page](https://ollama.com/pricing),
checked 2026-10-05. The smoke enforces five model requests, 4,096 total tokens,
1,024 output tokens, US$0.01 estimated cost, and a 60-second run deadline.

The updated `scripts/run_pilot_live_smoke.sh` was then exercised end to end.
Its rejection path made no request; its affirmative path repeated the bounded
smoke successfully with one fixture Action call, three provider calls, 1,973
tokens, 4.552 seconds, and estimated US$0.0035302. Across these two
consecutive runs, the recorded estimate is US$0.007254 under the same
uncached-input standard rates.

This is one live adapter/capability smoke, not the planned 10 scenarios × 5
runs, a real Serper search, a matched legacy comparison, or a browser test. The
focused live test and launcher passed; PIL-07 and the live-evaluation item
remain partial.
