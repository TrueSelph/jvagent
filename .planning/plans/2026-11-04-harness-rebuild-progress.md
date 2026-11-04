# Harness rebuild execution record

## W0 started

Branch: `codex/pydantic-harness-rebuild`; source baseline `7d689e4d`.
Governing objective: [execution plan](2026-11-04-harness-rebuild-execution.md).
The goal tracker rejected create_goal because an unfinished paused goal exists.
It has not been falsely completed. The repository plan governs resumed work.

### Dependency qualification

PyPI metadata and actual isolated installation selected these candidates:
`pydantic-ai-harness[skills]==0.66.0` and
`pydantic-ai-slim[openai]==2.66.0`. Both require Python >=3.11;
JV currently declares >=3.10. Plan an explicit Python support-floor migration.
Environment: `/private/tmp/jvagent-harness-rebuild-venv`, Python 3.12.15.
Editable JV installation succeeded; `uv pip check` passed with 80 installed
packages. This is dependency coexistence, not runtime or release qualification.
No project dependency pins or existing virtualenv were changed.

Native Agent + Skills discovery + TestModel(call_tools=[]) completed with two
messages. This is a deterministic API probe, not a provider/browser smoke.
Probes exposed two integration requirements:

- Skills requires an attached workspace. LocalWorkspace worked for discovery;
  production must use scoped assets and permissions, not repository-wide access.
- Official Skills warns that it ignores `allowed-tools` behavioral frontmatter.
  JV must enforce skill-specific Action tool exposure through a public toolset
  adapter. Loading skill text alone does not preserve JV authorization semantics.

A TestModel attempt with automatically generated load_capability arguments hit
tool retry exhaustion; actual skill loading remains unqualified and requires a
controlled explicit tool-call fixture, followed by actual-provider browser proof.

### Inventory

[Machine-readable baseline](2026-11-04-harness-baseline.json): 58 Action manifests,
22 SKILL.md assets, 17,695 Python lines under the Orchestrator. Each item is marked
for review; this is not a completed compatibility audit.

The pre-existing `jvagent/harness/` also contains custom admission, leases,
invocation and checkpoint machinery (`runtime.py:1`, `provider.py:1`). Audit its
host-policy invariants for extraction; do not create a third runtime beside it.

### Remaining W0 gate

Enumerate persisted execution settings and import consumers; classify each
extension; qualify lowest supported Python; reproduce measured workload baseline;
reconcile late acceptance records; define performance acceptance thresholds.
W1 and real browser/model qualification have not begun. No runtime changed in
this increment. Planning/progress files are uncommitted; repository commit gates
must pass before a milestone commit/push.

## W1 initial native composition component

Added `jvagent/harness/native.py`: official Agent/Skills composition, explicit
selected-skill tool names, schema-based native tool adaptation, per-invocation
access check and read-only tool admission. No legacy/pilot execution imports.
This is a development component, not connected ingress or a complete W1 gate.
The workspace is a read-only local parent directory for library discovery; this
is not sandbox isolation. Production asset scoping remains required.

Two deterministic FunctionModel regressions load a skill through the library's
`load_capability` protocol and exercise allowed/denied Action invocation. Both
passed in the isolated qualified environment. Undeclared tools are absent from
the model surface. The native suite skips where the optional official Harness
package is unavailable. Full repository gates, live-provider browser smoke,
usage/event delivery, shared budgets and persistence remain outstanding.

## Active-goal continuation: dependency and admission gates

The user cleared the prior goal and activated the rebuild goal; tracker replacement
is no longer blocked. The previous implementation turn made code progress; this
turn extends it rather than restarting it.

Pinned native-harness extra: core[openai] 2.66.0 and Harness[skills] 0.66.0.
Project Python floor and mypy target now 3.11. The pilot extra remains transitional
and conflicts with the native extra; use separate environments until deletion.
Editable native-extra install and pip check passed (85 installed packages including
test dependencies). Five isolated native tests pass. Authority arguments are now
rejected before Action dispatch; ambiguous wildcard declarations fail admission.
Full pytest and repository pre-commit were launched; results below must be
reconciled before committing. Initial pre-commit observed a concurrent test edit
and must be rerun cleanly.

The native component has not yet reached its required live browser gate: no
provider-backed native UI workflow, persistence or usage delivery is claimed.
Provider key fields are populated in examples/jvagent_app/.env (values were not
printed); actual validity must be tested through the disposable native app.

## Native run lifecycle adapter

Added run_native_agent: host-established run/conversation identifiers, typed
message-history forwarding, native streaming handler, native UsageLimits and
native timeout. It returns the full AgentRunResult for host persistence/egress;
it does not replace errors with partial text or implement a second agent loop.
Nine native regressions pass, including streamed events, message carry-forward,
usage receipts and invalid limit rejection. No browser qualification yet.

The full existing-environment suite completed with exit code 0. Its native tests
are skipped because the official Harness dependency is absent there; nine native
cases are separately executed in the qualified Python 3.12 environment. Optional
external-provider/PostgreSQL/Redis gates remain skipped in the ordinary suite.
Pre-commit previously passed; rerun after this lifecycle adapter before commit.

## JV native interaction bridge

Added the standard four-file `jvagent/native_harness` Action package and a declared
Interaction.native_run graph field. The development Action resolves existing
host Actions and ReplyAction, checks caller identity, refuses a second harness,
passes typed previous messages to the native run, stores output/messages/usage
before delivery, and retains pending-delivery state if publication is unconfirmed.
Previous unsettled native runs stop new execution pending reconciliation.

Eleven isolated native tests pass, including receipt-before-delivery and uncertain
delivery retention. Importing NativeHarnessAction in the qualified Python 3.12
environment succeeds. This is not crash/restart or browser qualification.
The bridge still needs UI events, standard model-attempt/cost observability,
shared spend enforcement, skill/action capability admission and failure replies.
It is a development replacement Action, not yet suitable for user deployment.
The action-authoring and memory guides were read before adding the package/field.

## First actual-provider native browser smoke

Disposable app `/private/tmp/jvagent-native-rebuild-smoke`, app-local JSON graph
`jvdb/`, API 8130, jvchat 3130. NativeHarnessAction and ReplyAction only; native
OpenAI GPT-4.1; simple casual SKILL.md. Source baseline 7d689e4d plus staged rebuild
changes. Browser authenticated and submitted real requests. OpenAI rejected the
configured key with HTTP 401 (invalid API key). No successful output, tool usage,
token receipt or cost is qualified. Three diagnostic requests took place while
fixing error publication; no more equivalent provider attempts are warranted.

Browser exposed an empty-error reply and incompatible delivery message ID.
Fixed bounded credential-error publication and `o.ResponseMessage.` identifier
mapping. Final request "What is 6 plus 7? Please answer briefly." displayed the
credential rejection in jvchat. Graph readback: Interaction
n.Interaction.f5f1f5e8b8514f8290bda1ce, run 9aa6a95401a0496c84456107bd111305,
status failed, usage_complete false, closed true, emitted true. UI/server timestamps
place submission and closure within the same minute; exact elapsed duration was
not instrumented and is unqualified. Unknown spend is not a zero-cost claim.

Added tool-event response-bus mapping and successful model-message receipts feeding
Interaction.compute_usage. Eleven isolated tests pass, including production-shaped
message-ID assertions. Positive-path UI events/usage remain unqualified. First
pre-commit found missing tools annotation; corrected. Rerun required after edits.
A valid OPENAI_API_KEY is required before successful provider/browser qualification;
the populated example key is not accepted. Other implementation work can continue.

## Official persistence interface adapter

Added InteractionStepStore implementing the public official Harness StepStore
protocol on Interaction.native_steps; native composition now installs the
StepPersistence capability when this store is supplied. The library owns events,
settled snapshots and effect state; the adapter serializes its public records.
The existing final receipt remains for JV delivery status. Duplicate message
storage in that receipt is transitional and should be removed after native
snapshot-based cross-turn loading is qualified.

Fourteen isolated native tests pass: actual StepPersistence run, JSON serialization
and restoration, event idempotency, run filtering, unresolved effect replacement,
and default exclusion of interrupted snapshots. These storage tests use an
injected save boundary; they do not establish real graph crash/transaction,
restart, concurrency or distributed lease safety. Adapter requires a single
active writer and has no production retention policy yet. Do not deploy across
workers before fencing qualification.

Pre-commit passed before the final additional test; rerun before committing.
Live positive-path browser qualification remains blocked by the previously
confirmed invalid OpenAI key. No equivalent known-failing provider call was
repeated in this storage round; the latest browser evidence remains the prior
credential-error flow, not qualification of new step persistence.

## Settled snapshot readback and development checkpoint

Removed the duplicated message list from native_run; subsequent turns now load
history from the official StepStore settled snapshot and reject missing snapshots.
Added a real JsonDB/Interaction test: execute official StepPersistence, save records,
replace the GraphContext and JsonDB instance, reload Interaction by ID and verify
typed messages and events survive. Fifteen native tests pass in the qualified
Python 3.12 environment. This is fresh-context graph readback, not process death,
transaction interruption or production backend qualification.

The pending checkpoint is explicitly a development foundation. Its live browser
evidence is a bounded HTTP 401 failure, not successful generation; that gate is
open pending valid credentials. Current running disposable backend may predate
these storage changes and must be restarted before a future positive smoke.
