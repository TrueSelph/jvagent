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

## Published foundation and explicit skill admission

Foundation commit 65795d4a pushed successfully to origin/codex/pydantic-harness-rebuild
following clean pre-commit and full pre-push pytest. Local/remote SHA confirmed.
The subsequent admission changes are not included in that published commit.

JV skill declaration admission is now separated from official skill execution:
requires-actions and requires-tools must be satisfied; required tools must be
allowed; allowed/denied channel lists are enforced. Unknown spec, flow/prerequisite
or extension semantics and output contracts without a qualified capability are
rejected before model execution. In particular, research cannot silently lose
its evidence_required contract: it stays unavailable through the native Action
until the research capability is integrated. All 26 native tests pass, including
11 added admission tests. These are deterministic policy tests, not provider or
browser qualification. The known-rejected OpenAI credential is unchanged; no
redundant live-provider request was made. Missing valid credentials remains a
positive browser gate, not an excuse to drop protection or broaden admission.

## Research policy extraction

Moved typed evidence references/findings/briefs, URL normalization, freshness and
quote/citation rendering into jvagent/skills/research/contracts.py. Moved source
collection into research/evidence.py. Pilot contracts/runtime retain explicit
compatibility imports and their conversational renderer; no second validator was
introduced. Native research admission remains gated until output/tool-observation
hooks are connected. The extracted modules import no pilot runtime.

Existing evidence/contract slice passed (45 tests); the complete pilot test slice
also exited 0 with opt-in external gates skipped. The 26 native tests pass.
No model semantics were changed by extraction and no repeat of the invalid-key
browser call was made. This refactor is source-test evidence, not native research
browser qualification. New and moved files must pass repository hooks before the
next checkpoint; successful native research remains an explicit open gate.

## Native research output policy connected

Installed a per-run skill output policy registry with evidence_required implemented
by research/native.py. The core factory supplies the policy's typed output function
to Pydantic AI and forwards successful tool results with host metadata for evidence
observation before source-ID annotation. Final research output uses the existing
source/freshness/quote validator and host citation renderer; ordinary skills retain
plain text output. Unknown output contracts remain rejected. No pilot execution
imports are required by the native research policy.

Native regression exercises a reviewed read Action fetch receipt through the
native tool surface, typed output-function call and rendered citations. Another
rejects a fabricated source ID. This is deterministic FunctionModel evidence,
not actual provider or browser qualification. Official Skills also requires name
and directory agreement; factory now rejects mismatches instead of accepting a
loader warning that silently skips the skill. Some existing assets will need a
small naming migration or a scoped asset-normalization adapter.

The known-invalid key still blocks successful native research/browser receipts;
no equivalent rejected request was repeated. Runtime compatibility is broader,
but general shared budget/recovery/deployment gates remain open.

## Official spend policy integration

Native composition now installs official SpendLimits with run/conversation dollar
budgets (development defaults USD 2 / USD 20) and on_unpriced='raise'. A declared
Conversation.native_spend field backs its BatchSpendStore interface, including
replay tokens and expiry metadata. Invalid/negative/nonfinite amounts fail closed.
The adapter is single-writer: no distributed reservation/fencing claim is made.
The library checks accumulated spend between requests; in-flight/concurrent
overshoot remains possible and must be documented and qualified.

Native tests include serialization/reload, deduplication, invalid increments and
an actual SpendLimits integration refusing a later run after a prior response
crosses the conversation ceiling. Old conversation dollar totals are not migrated
into these new counters yet: existing-session cutover remains prohibited until
W5 migration reconciles them. Live pricing, unpriced-provider receipts and billing
reconciliation are still unqualified due to the invalid model credential.

## Cancellation and publication uncertainty

Native cancellation settlement now shields the graph write through repeated caller
cancellation, logs only run ID and exception type on write failure, and preserves
the original CancelledError. Cancellation once final publication may have started
records delivery_unknown, retaining output and complete known usage instead of
labeling it a safely replayable cancelled run. No automatic resend is implemented.

All 36 native tests pass, including cancellation during native output publication,
second cancellation during persistence and persistence outage. These are injected
boundary tests, not real disconnect/network delivery or backend crash evidence.
The single-writer/lease, deadline and manual reconciliation UI gates remain open.

## Compatibility inventory and pre-measurement acceptance

Added a complete named pre-rebuild inventory: 58 Action dispositions, 22 skill
assets and 91 persisted Orchestrator settings with verified file/line references.
All migration implementations remain explicitly false/pending; categories do not
claim qualified compatibility. The new native Action is tracked separately from
this baseline. Required next migrations include script/flow semantics and skill
name normalization; no obsolete setting may silently disappear.

Added qualification matrix and workload thresholds before performance measurement:
20 scenarios x 3 repeats per implementation; all deterministic safety gates,
>=95% scenario success, no quality regression, pilot p95 latency <=1.2x baseline
and median tokens <=1.1x. These are project acceptance choices, not measured results
or an SLA. Real-provider/browser baseline, exact mapping implementation and large
soak qualification are still open. Asset paths, name uniqueness and all 91 setting
source references were mechanically verified. Runtime source was left unchanged
while the 056ef283 pre-push full suite ran.

## Python floor and dedicated CI qualification

Fresh Python 3.11.15 environment `/private/tmp/jvagent-native-py311` installs
native-harness, passes dependency check (85 packages including tests), and passes
all 36 native tests both in isolation and with normal repository conftest fixtures.
The earlier Python 3.12 environment also passed those cases. Added native CI
matrix for 3.11/3.12; remote CI has not yet run.

Attempting the exact initially drafted CI install `.[test,native-harness]` failed
resolution: native core needs OpenAI SDK >=3.31, legacy pinned LiteLLM needs <3.
Corrected the lane to install native-harness and its minimal test dependencies.
This is an explicit transitional incompatibility, not unified dependency proof.
W5 must remove obsolete transport/test dependencies before release. No source
runtime changed and no invalid-key provider request was repeated in this round.

Push 056ef283 completed successfully after the full pre-push suite passed; local
and origin SHA matched. New inventory/CI changes remain separate from that commit.

## Ollama Cloud browser and actual Conversation field correction

Tested native `ollama:glm-5.3:cloud` with official OllamaProvider targeting
https://ollama.com/v1, using the separately configured cloud key (no local model).
First browser request exposed a real schema defect: native_spend had mistakenly
been inserted in Conversation's docstring, not its declared fields. Corrected the
actual attribute declaration and added a real Conversation/JsonDB fresh-context
spend round-trip test. Earlier SimpleNamespace budget tests could not catch this;
this is why UI/graph testing remains mandatory.

Restarted the disposable app from 52ee27b1 plus the field/test fix. Actual browser
request "Reply briefly: what is 22 plus 23?" reached the provider and returned
HTTP 401. Interaction n.Interaction.d4e373a625cd4be089d049d2, run
b10b07e1971e4190a15fefdef339fb17, completed in 2.804 seconds from persisted
start/completion stamps: failed, closed=true, emitted=true, usage_complete=false.
Official step log contains run_started, model_request_started,
model_request_failed and run_failed. Browser displayed the bounded credential
error. No provider token/cost receipt was available; spend is unknown, not zero.

The pre-provider schema-failure interaction was
n.Interaction.4193fb0138c44d63bf05db96 (0.954 seconds). No authentication claim
was made from that earlier failure. Both configured OpenAI and Ollama Cloud keys
now have actual 401 evidence. Requested an updated key asynchronously; do not
repeat equivalent rejected calls until credentials change.

52ee27b1 pushed successfully after its hook completed. Current field correction
requires fresh native/memory tests and hooks before its checkpoint.

## Native import isolation and shared access service

Made legacy HarnessRuntime/HarnessStore/get_runtime/reset_runtime package exports
lazy while retaining their public import identities. Native module/Action imports
no longer initialize the custom harness runtime or old Orchestrator/pilot modules.
Moved shared Action access checks to tooling/access.py; old access.py reexports
the same functions and retains the existing logger/event taxonomy for compatibility.
This removes native dependence on importing the Orchestrator package to authorize
a tool. Host walker/embedded admission still uses transitional runtime services;
this is not a claim that the old runtime has been fully removed.

Implemented and tested in a disposable detached worktree while the prior push hook
ran, then applied its staged patch to the main rebuild worktree after that push
completed. 44 native/access tests passed on Python 3.11. Harness/conformance slices
also passed in the existing environment, where optional native tests skip and are
separately covered by the qualified environment. Added a fresh-subprocess import
regression to detect future eager-loading regressions. Temporary worktree hooks
passed after restaging formatter changes. Root hooks must also pass before commit.
No provider behavior changed; both known-invalid credentials remain unsuitable
for a positive model/browser test. No redundant provider calls were made.

3df85b45 is confirmed pushed after all pre-push hooks passed.

## Positive OpenAI browser qualification and earlier provider correction

User updated OPENAI_API_KEY. Isolated app reloaded the key without exposing it.
Actual GPT-4.1 browser chat loaded the casual skill and delivered "31 plus 12 equals
43." Interaction d0b763a47efd4171802ac969 / run 2132a02355714fc2b48f179bcdf0bf63:
5.756 seconds, 2 requests, 487 tokens, USD 0.000514 estimated. Expanded UI showed
per-request tokens and estimated cost; per-model timing remains absent. Follow-up
"What number did you just calculate?" returned 43 from persisted library history:
Interaction 5df5339503d146a8b413f952, run d406179ce32a4250910c2619634e43e5,
0.923 seconds, 312 tokens, USD 0.000315 estimated. Both closed/emitted/delivered.

CORRECTION: merge-mode bootstrap had retained the stored OpenAI model when the
prior round attempted an Ollama switch. Persisted error records for interaction
d4e373a625cd4be089d049d2 explicitly name gpt-4.1, not GLM. Therefore that 401 is
OpenAI evidence; the earlier claim that this round independently qualified an
Ollama authentication failure is withdrawn. Ollama Cloud remains untested by this
attempt. Config changes must be read back before attributing provider results.
The same merge policy retained the casual skill; source-sync was explicitly
confirmed on this disposable graph and persisted research path/model read back.

Initial live native research (ac38982f5b92482d8afd33c1) searched/fetched successfully
but failed output validation because supporting_source_id was optional in the
archived pilot schema and the model omitted it. Added native-only GroundedFinding/
GroundedBrief schemas requiring source ID and quote; historical pilot data remains
readable. Added schema regression. This failed run also exposed missing failure-
path usage aggregation: model spend exists in snapshots but the UI showed zero.
That defect remains open and must be fixed, not interpreted as zero spend.

Retested actual research in browser with exact single prompt. Interaction
da678537d3dc46c884219173 / run c0777134b20341b59fbf66e70af40ba0 delivered a
validated python.org citation; 15.710 seconds, 7 model requests, 16,765 tokens,
USD 0.017232 estimated. UI showed five tool calls (search + four fetches) and
stats. One quote-mismatch repair occurred. The output renderer does not display
the supporting quote requested by the prompt, though it validates/stores it;
quality and efficiency follow-ups remain open. No baseline performance win is
claimed. General existing non-research skill, broader providers, repeated-workload
quality, failure accounting, events/timing and enterprise gates remain unqualified.

Abrupt-exit tests now exercise actual child-process os._exit against JsonDB:
settled messages survive and continue_run reads them; death inside the first tool
leaves a started unresolved effect and no default continuable snapshot. This does
not qualify PostgreSQL, in-transaction death or automatic safe effect replay.

## Failure usage and native pricing provenance

Added native accounting adapter that records only matching run_id response receipts,
deduplicates settlement and excludes prior history. Failed output validation now
loads receipts from the official at-failure snapshot; model requests without a
response receipt are explicitly represented as unknown spend. Native usage.cost
is preserved as a conservative pricing estimate rather than silently using JV's
generic fallback or claiming provider-billed actuals. Cancellation-time accounting,
provider SDK internal retries and per-request timing remain separate open gates.

44 native cases pass, including failed validation with/without prior history,
repeat settlement, missing-receipt uncertainty and real abrupt-exit recovery.
Actual browser regression used disposable native research with max_requests=1:
Interaction n.Interaction.9e94a8359a75483f90217129, run
845357548aea48a398ff6fd41eef129e, failed terminal in 4.591 seconds after one
reported model response and search. UI displayed 558 tokens and USD 0.00129
estimated; graph usage matched (529 input, 29 output), closed/emitted=true,
usage_complete=false. No zero-cost claim is made from incomplete usage.
The generic terminal error incorrectly suggests configuration for a budget stop;
user-facing error classification remains to improve. Request limit restored to 20
using disposable graph source sync; positive skill/research behavior remains as
qualified in the preceding browser round. Newly observed pricing supersedes the
generic-estimator amounts for future runs; prior smoke amounts remain historical.
