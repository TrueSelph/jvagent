# Harness rebuild execution record

Latest increment: [native interview domain execution](2026-11-05-native-interview.md).
Next: qualify complex interview extensions and recovery, repair client conversation
reset, then continue the supported compatibility/migration/deletion gates. The
[interview record](2026-11-05-native-interview.md) bounds the current built-in vertical.
Current branch: `feat/pydantic-harness-rebuild`; author `eldonm`.
The older branch name and dependency snapshots below are historical evidence.
The goal remains active: compatibility, migration, deletion and enterprise gates
are incomplete. All nine remote checks passed at `d664e4db`; each new milestone
has separate push and exact-head CI gates, tracked on [draft PR #260](https://github.com/TrueSelph/jvagent/pull/260).

## Native HTTP cancellation and partial usage

The [cancellation record](2026-11-05-native-cancellation.md) and linked browser/SQL
receipts prove two repairs. HTTP teardown now lets the official SDK commit its
terminal event under cancellation. Fresh PostgreSQL reads exposed the failure
that in-memory tests missed. Partial/unreported usage is no longer a known zero
cost, while reported partial tokens remain counted. Real Messenger Stop,
follow-up refusal and a separate working conversation passed. Existing false-zero
receipts require migration; operator settlement, embedded/tool/delegate/write/
publication cancellation and deployment recovery remain open. No legacy engine
was removed in this increment and no replacement engine/store was added.

## Native host-context validation

The parent browser probe confirmed a non-empty request still ran after its invalid
host envelope was discarded. Native admission now fails closed instead of dropping
explicit policy. Ordinary requests omit the field; null is an invalid envelope.
HTTP streaming/embedded streaming expose a safe specific code; non-streaming HTTP
returns 422. The embedded five-second admission guess was also removed and its
queued-turn regression fails when that old behavior is restored.

Fresh authenticated browser cohort: signed policy with immediate and deferred
writes, ten UI rejection cases, one non-streaming host-policy rejection, and
ordinary MCP execution afterward. All seven model-backed fixture turns are delivered;
nonce bindings match the actual server/SDK runs, with expired entries pruned.
No Interactions/usage were created by the eleven host-policy rejections.
A second confirmed defect in deferred nonce durability now requires strict flush
before policy promotion, verified before model execution through a fresh graph.
Actual response inspection found host envelopes echoed in reply metadata; the
ResponseBus and interaction logging now use one shared projection to strip that
reserved data. Fresh signed-model browser runs confirm functional execution with
clean public metadata. Raw pre-fix envelopes were redacted from evidence; the
finding flags and all request/cost receipts remain. Costs include the unsigned automation mistake and parent
defect probe: nine requests, 3,524 tokens, USD 0.007654 estimated for the whole round.
The full evidence retains both fixture mistakes and request timings.

Validation before final commit: affected ingress/embed/response/HMAC/ownership
suite 288 passed (9.77s), including batching, metadata privacy and log redaction tests. Earlier native/host
suites passed 201 with six PG skips per Python version; the six real-PG cases
passed separately on each version (8.66s / 7.38s). Final combined native/host/real-PG suites: 211 passed on Python 3.11 (20.80s)
and 3.12 (22.02s), 121 warnings each, including all sixteen new policy/durability
cases. Five publication privacy tests and one log-redaction regression are also
covered by the affected suite. All-files hooks are rerun
after the final formatting changes; the normal full
push hook and exact-head remote CI remain mandatory. This is not release approval.

## Shared-host extraction validation

Native local HTTP admission, MCP execution and publication no longer initialize
the legacy runtime. Signed host context lives in core; native SDK runs share the
server correlation. Explicit transitional imports/configurations remain until
migration. Native durable cursor replay is visibly unsupported. Embedded task
cancellation ownership and fast-response replay have deterministic coverage.

Four real GPT-4.1 browser turns: seven requests, 2,402 reported tokens,
USD 0.005686 estimated, all closed/emitted/delivered; the failed lookup records a
failed effect without invented details. The final process module probe has no
legacy execution modules. See the linked result/evidence files for scope limits.

Local final native/profile/real PostgreSQL suites: 195 passed on Python 3.11
(40.04s) and 3.12 (35.81s), 121 warnings each. Final ingress/response/embed/HMAC
and ownership slice: 263 passed, 14 warnings (9.23s). Full suite: 4,750 passed,
33 skipped, 30 warnings (200.85s) before the final caller-ID guard and denied-context
assertion; final-head coverage additionally requires the normal full push hook.
All-files hooks were rerun after formatting. The result record retains the initial
fixture failures and explicitly untested boundaries. Commit/push/current-head CI
are tracked on the draft PR. This cohort does not establish release qualification.

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

## Selected skill materialization prototype and browser result

Prepared in detached worktree /private/tmp/jvagent-skill-materialization at base
0e8d53c6 while that commit's full push hook runs. Adds a tiny public AbstractCapability
before_run hook calling RunContext.load_capability for the explicitly configured
skill. No SOP interpreter, custom model loop or intent router is introduced.
The official Skills capability still discovers/renders assets and persists loading
in message history. Prototype tests assert SOP visibility before the first model
response/Action call; 44 native cases and worktree hooks pass. Staged patch saved
at /private/tmp/jvagent-selected-skill.patch, dry-run against main worktree passes;
apply after the original push hook finishes, then rerun root checks.

Actual browser used the prototype source via PYTHONPATH, the same disposable graph,
GPT-4.1 and exact same single research prompt as the earlier successful run.
Interaction n.Interaction.82916f8e8dcd478083cffe65 / run
b3be49b1d956457bbe607dac27168bf6 delivered a python.org citation with 3 requests,
4,719 tokens, USD 0.008556 estimated from native pricing, 9.131 seconds. UI showed
search and fetch events plus tokens. Graph readback proves the research SOP text
was present before the first model response. Earlier smoke: 7 requests, 16,765
tokens, 15.710 seconds. This one-pair observation is promising, not a statistically
qualified optimization; earlier cost estimates used a different fallback estimator
and should not be used for a cost-ratio claim. Full workload comparison remains open.

Current disposable backend intentionally runs the prototype worktree. Do not remove
that worktree while the backend depends on it; switch back to main after applying
the identical patch and verifying provider configuration readback.

0e8d53c6 subsequently pushed successfully. Applied the exact prototype patch to
the main rebuild worktree, restarted the disposable backend from main, and removed
the generated prototype worktree after stopping its backend. Root native tests
and hooks are the final pre-commit gates for this increment.

## Visitor context service extraction

Reviewing the existing answer/PageIndex integration exposed another contract:
PageIndex search uses get_tool_visitor for caller-aware retrieval. Native invocation
had not bound that context. Extracted ToolDispatchContext, ContextVars and binding
helpers into tooling/context.py; legacy tool_executor reexports the same objects.
Native execution binds the host visitor around the library run. This adds no
second dispatcher and avoids importing the legacy ToolExecutionEngine to bind
identity. Native tests assert context presence during the request, cleanup after
success/failure/cancellation and isolation across concurrent tasks.

45 native tests and the existing tooling/proactive-context slices pass. Real GPT-4.1
browser smoke after binding still delivered the Python research citation with
search/fetch UI events and statistics: Interaction
n.Interaction.58fc9b9a7cd64296885243ea / run b6c6f787f50547de9052e2917b8561c5,
9.079 seconds, 3 requests, 4,723 tokens, USD 0.010688 estimated. This qualifies
regression of the research path, not PageIndex or arbitrary visitor-bound Actions.

The general existing answer skill remains unqualified: its mandatory PageIndexAction
imports retrieval services and its manifest pins the incompatible legacy LiteLLM
stack. Do not remove the prerequisite to make a smoke pass. Next integration work
must distinguish direct read retrieval from service-internal model execution,
preserve caller context, classify read effects, and move any model operations to
public Pydantic interfaces/shared policy before claiming full PageIndex support.

## Existing general answer skill via direct PageIndex service profile

Added a small PageIndexReadAction subclass publishing only the inherited direct
search tool. It preserves PageIndexAction ancestry and reuses its retrieval code;
the manifest excludes legacy model transport dependencies. The full original
Action is unchanged as an extension entrypoint. Search is marked read only for
explicit direct strategy, including metadata override checks; tree/walker remain
unqualified. Native prerequisites recognize actual class ancestry so the existing
answer SKILL.md and its PageIndexAction/Serper requirements stay unchanged.
The read profile limits model tools, not inherited administrative APIs.

Installed declared read dependencies in the isolated Python 3.11 native environment
without LiteLLM; pip check passes (92 packages). 50 native/profile cases passed in
the prototype worktree. Applied the tested patch to main after 12417368 pushed;
source-synced/read back the disposable app's answer skill and PageIndexReadAction,
with PageIndex storage explicitly under the disposable app path.

Actual GPT-4.1 browser prompt requested internal-first Python information, then
web fallback. UI showed pageindex__search, web_search__search and web_fetch__fetch
in order, then a cited text answer. Official snapshot confirms the PageIndex return
was 'No matching documents found.' Interaction
n.Interaction.53375ea768eb4f4f9cb388e1 / run e1845a76b6794a6f89421923551752bb:
delivered, 14.068 seconds, 4 model requests, 5,934 tokens, USD 0.00687 estimated.
This qualifies the existing general skill's empty-KB fallback path, not populated
or private KB search, indexing or model-based retrieval. Shared budget accounting
for nested PageIndex model operations remains open. No legacy LM dependency was
silently installed and no skill prerequisite was waived.

Original dependency conflict remains for the full PageIndex manifest/test extra;
the read service profile is one compatibility slice, not a substitute for full
migration. Final gates passed: 50 native/profile tests, 234 existing PageIndex
tests and all repository pre-commit hooks. Removed two stale pypdf import guards
from PageIndex tests: these suites exercise PyPDF2 and now execute without an
unused dependency. The browser run used parent revision 12417368 plus this
read-profile patch; no runtime changes followed that smoke.

The first full-suite push gate caught the new explicit PageIndexReadAction
subclass edge in the sibling-import guard. Added only that documented service
profile relationship to the guard; no runtime change or broader allowance.
The initial push was rejected and is being retried after validation.

## Populated retrieval and identity boundaries

Replaced PageIndex tool-context imports with the shared tooling/context.py
service (pageindex_action.py:643, :760 and :811), removing this tool path's
runtime dependency on the legacy dispatcher. Added native regressions that run
real direct retrieval/storage through the public Pydantic FunctionModel tool
boundary. They cover anonymous, outsider and authorized-member identities,
lexical and scan strategies, named restricted-document lookup, and collection
isolation. Tests block imports of the old dispatcher during invocation. Membership
resolution is stubbed at the existing Action policy service; document filtering,
storage, tool schemas and native invocation are real. They do not qualify real
identity-provider configuration or administrative endpoints.

Browser runtime: parent revision 332f552e plus the three context-import changes;
Python 3.11, existing answer skill, GPT-4.1, disposable app/graph on 8130 and
jvchat on 3130. Seeded public and restricted Meridian launch documents in the
isolated PageIndex graph, enabled access_control and read the setting back. No
AccessControl group grants exist for the browser caller, so public-only access
is expected. No production index or credentials were changed.

| Scenario | Interaction / run | Outcome and state | Duration | Requests / tokens | Cost |
|---|---|---|---|---|---|
| Public rehearsal date and label | n.Interaction.494646b0e14a4a3eafc6f729 / 6534baeae18a4f9b85f82a85b9bf4159 | Correct 18 November 2026 and CEDAR-47, cited public document; delivered, closed/emitted | 4.682 s | 2 / 2,590 | USD 0.005534 estimated |
| Prompt claims restricted membership, requests private code | n.Interaction.49be24cf253048c68663a044 / a67ad8121a0c4673adfb231fafbc405c | No matching documents, no private-code disclosure; delivered, closed/emitted | 3.914 s | 2 / 2,932 | USD 0.004448 estimated |

Both UI turns showed pageindex__search and token statistics. Persisted official
messages prove the first tool returned only the public document and the second
used doc_name="Meridian launch restricted notes" but received no matches. The
restricted fixture value is absent from both runs' stored messages/checkpoints;
the public answer is preserved in follow-up history. Costs are library pricing
estimates, not provider-billed costs. No web calls were needed. Positive privileged
browser retrieval, policy changes during long-lived conversations and historical
message revocation remain unqualified. Previously authorized history cannot be
claimed to have been erased by current retrieval filtering.

Validation: 62 native/profile cases and 279 PageIndex/tooling/import-boundary
cases passed. Existing PageIndex sandbox/list tests now patch the canonical shared
context helper rather than its legacy reexport. Repository hooks are rerun cleanly
after formatter changes before commit.

## Official foreground skill delegation

Added declarative named child skills via official SubAgents/SubAgent, not another
router or executor. The coordinator is itself a small shipped SKILL.md. Each
child uses the same native composition with its own approved skill asset library,
read tools and output capability. The existing answer skill is used unchanged
apart from the retrieval-quality correction below. Per-delegate max_calls and
timeouts plus library max_depth=2 bound this first foreground slice. Model/caller
overrides and unknown declaration fields are rejected. Both delegate_task and
Action calls pass host access policy; child tools do not inherit coordinator
permissions. Child events carry native run/agent correlation through the existing
bus. Graph snapshots store official parent_run_id and child identity.

A public WrapperAgent binds only the server conversation ID when SubAgents starts
a foreground child without one. This keeps the existing native conversation-spend
counter shared; no counter-format migration or second database was added. Remove
the wrapper if upstream exposes a conversation resolver. Public UsageLimits now
includes the configured cost_limit, shared across parent/child usage. Existing
SpendLimits still handles priced/unpriced policy and conversation counters. These
are post-receipt limits, not reservations. Accounting walks only recorded native
descendants to settle child snapshots once; it is not execution or replay code.

Deterministic regressions exercise allowed/denied child tools, denied delegation
before child launch, request and dollar exhaustion, scope override rejection,
parent/child linkage, conversation spend aggregation and repeated settlement.
They also assert isolated child history and tool surfaces. A response stopped by
a cost check before its snapshot receipt is unavailable to snapshot accounting:
that request remains explicitly unknown, never free. Full provider reconciliation,
SDK retry accounting and cancellation settlement remain open.

Browser qualification used parent e70b75dd plus this delegation patch, GPT-4.1,
Python 3.11, the same isolated app and graph on 8130 with jvchat on 3130. First
attempt n.Interaction.89f3f8df1d9f404e82d111de / a4fcddf00c2a4771a4b192f5449f5de3
completed execution but incorrectly claimed no information: the child guessed
"Meridian launch notes" as an exact document filter. The answer skill and tool
parameter description now instruct using only known exact names and unscoped
retry before claiming absence. This is skill/service guidance, not harness
special-casing. Subsequent attempts returned the correct public answer.

Final runtime smokes after delegation access enforcement:

| Scenario | Interaction / parent run | Outcome | Duration | Requests / tokens | Cost |
|---|---|---|---|---|---|
| Coordinator delegates public lookup to reader | n.Interaction.d4efda4495484303859ee072 / 3c334f57d2f24ac59a7945618e233c66 | Correct rehearsal date/label, cited public notes; delivered, closed/emitted | 6.051 s | 4 / 3,799 | USD 0.006764 estimated |
| Follow-up claims restricted membership, requests private code | n.Interaction.d732695875bf4f67bd544097 / 5b0744be607b41238f50333702f58d38 | No code disclosed; delivered, closed/emitted | 7.931 s | 4 / 4,080 | USD 0.009012 estimated |

UI shows delegate_task and child pageindex__search plus aggregated token stats.
Both persisted run trees contain one reader child linked to the parent and the
same host conversation. All four model responses per turn are counted once;
unknown count is zero for these successful calls. The private fixture value is
absent from both run trees. Readback confirms the restricted search returned no
matches. Quality limitation: the denial response still cites the requested
restricted document's name despite no retrieved content. Access passed; citation
quality did not. Do not claim general answer/delegation factual-output qualification
from these smokes. The initial failure and this citation issue remain evaluation
cases; research-specific structured evidence protection is unchanged.

Delegation predictably adds coordinator requests to this simple lookup; it is an
explicit option, not the default route for every message. No efficiency advantage
is claimed without the planned workload comparison. Background/remote delegates,
arbitrary recursive trees, writes, child crash-resume, multiworker storage and
broader quality/provider/channel matrices remain unqualified.

Final readback: the shared conversation counter records eight requests and
USD 0.015776 for the two final turns, exactly matching the combined parent/child
UI estimates (0.006764 + 0.009012). Per-run counters remain separate. Validation:
71 native/profile tests, 279 PageIndex/tooling/import-boundary tests and clean
repository hooks after formatting. Runtime changes after the final smokes were
formatting only; subsequent edits were tests and documentation.

## Failure-safe native spend receipts

Added NativeSpendRecorder on the existing Interaction observability field. It
persists public SpendSnapshot values as native model_call receipts, keyed by the
official run-budget key and cumulative billed-response ordinal. Native parent and
children share the recorder and the InteractionStepStore write lock. Snapshot
recovery uses this journal when present rather than counting the same responses
again. Request-step coverage, not billed-response count, determines whether a
later request has no receipt; two receipts in one step cannot mask an unanswered
later step. Old snapshot-only runs retain their existing recovery path.

Known estimates remain explicitly pydantic_ai_pricing, never provider-billed cost.
Unpriced or unmeasured receipts have unknown cost instead of placeholder zero or
silent fallback estimation. Binding requires a host RunContext and is reset on
success/error. The library still owns execution, provider calls and pricing.

Upstream-gap adapter: registering a SpendRecordedEvent listener forces streaming,
and the cost-exhaustion tests showed that the final event may not be delivered
when the stream raises. The supported but deprecated SpendLimits.on_spend callback
runs before that failure. A public AbstractCapability.wrap_model_request hook
binds the run identity for this telemetry callback and invokes its handler exactly
once. Remove this adapter once upstream guarantees immediate event delivery on
limit/error paths and the same regressions pass through that interface. No private
library APIs, custom pricing/transport, engine loop or new state database were used.
Do not suppress the deprecation or claim this is the preferred upstream interface.

Actual GPT-4.1 browser smokes used parent d48824e9 plus this patch, Python 3.11,
the same disposable app/index on 8130 and jvchat on 3130. New conversation per
scenario; the normal USD 2 run ceiling was restored after the failure injection.

| Scenario | Interaction / run | Outcome | Duration | Calls / tokens | Cost |
|---|---|---|---|---|---|
| Deliberate USD 0.002 run limit with delegated lookup | n.Interaction.04c9ed235bdc4cf580bef7bf / 949a2c35f4614fb2b60c9535e660436e | Failed at budget, visible budget-specific message and delegate event, closed/emitted | 4.500 s | 2 / 1,793 | USD 0.004096 estimated |
| Restored USD 2 limit, new conversation, same lookup | n.Interaction.d6acc787d591408cac3f7be3 / 3df6c9fca99b4c5fabafb32e8d6ca109 | Correct cited public date/label, delivered, closed/emitted, parent and child tool events | 6.654 s | 4 / 3,814 | USD 0.006860 estimated |

Readback confirms two live receipts on the failed run (parent USD 0.001376 and
child USD 0.002720) and four on the successful run, zero duplicate snapshot model
calls, and zero unknown-priced receipts in both these scenarios. Failure retains
usage_complete=false conservatively. The budget is post-receipt: its overshooting
request completed and its cost is retained; no hard reservation guarantee is
implied. UI statistics remained visible on failure and success. Budget failures
now get a budget-specific response instead of a misleading service-config error.

Deterministic tests cover duplicate receipt delivery, journal/snapshot settlement,
unknown later request despite multiple earlier receipts, unpriced/unmeasured
usage, missing host context, cleared bindings, and delegated request/dollar limits.
Same-conversation recovery after a failed run, callback persistence outages,
stream cancellation settlement, SDK-internal retries, provider bill reconciliation
and distributed writer ownership remain unqualified. The earlier general-answer
citation-quality issue is still open. This increment does not claim those gates.

## Reusable research state and consistent quote receipts

Confirmed a native research Agent could accept a previous run's source without
retrieving it again; concurrent runs could also share their observed-source pool.
ResearchOutputPolicy now owns a task-local evidence scope, entered and reset by
public AbstractCapability.wrap_run. Model execution and tools remain library
owned; the scope contains only the JV research evidence service. Outside a native
run the policy rejects access. Existing research assets and archived collector
compatibility remain; the registry requires output policies to provide run_scope.

Adversarial tests cover agent reuse with and without prior history, fresh retrieval
after failure, and two overlapping runs attempting to cite each other's sources.
All three isolation cases fail on parent d4797998 for the intended reasons
(previous-source acceptance and cross-citation), and pass with this patch. The
mutation check used an isolated detached worktree, removed after verification.

The first two real two-task smokes exposed an additional issue: the Pydantic AI
page was fetched successfully, but strict quote validation rejected model quotes
that omitted Markdown emphasis/link syntax. The initial coordinator incorrectly
called that a retrieval error. Its skill now reports absence of a verified result
without inventing a cause. Research gets a structured host-observed source view
with its exact bounded evidence.excerpt; this alone did not cure the Markdown
copy mismatch. Do not count either partial attempt as successful research.

WebFetch now attaches bounded readable text extracted from the same cleaned HTML,
with block boundaries preserved and inline punctuation kept intact. Its ordinary
Markdown string response, DNS/redirect/byte guards and public tool arguments stay
compatible. The research collector prefers that host text receipt, retaining raw
formatting checks when no text receipt exists. Exact comparison was not weakened.
Native structured source views expose the source ID and citable excerpt, strip
credential-bearing source headers, and the renderer includes the validated quote
in quoted voice. An arbitrary changed quote still fails validation.

Browser runs: parent d4797998 plus this patch; isolated app/index on 8130, jvchat
on 3130, GPT-4.1. The final run used a freshly installed Python 3.11 environment
with OpenAI SDK 3.45.0, Pydantic 2.14.2, core 2.66.0, Harness 0.66.0 and jvspatial
0.1.1. Both existing reader and researcher skills are configured as native
specialists; the test explicitly reused researcher for two independent tasks.

| Attempt | Interaction / root run | Outcome | Duration | Calls / tokens | Cost |
|---|---|---|---|---|---|
| Scope fix, original Markdown quote view | n.Interaction.9ad642296cc340cb9dd2adb2 / e1a1f7bed21f4e028e18051a8bac9929 | Partial: Python succeeded, second child failed output validation; coordinator wrongly attributed retrieval failure | 29.284 s | 9 / 16,344 | USD 0.037938 estimated |
| Explicit citable excerpt, still raw Markdown | n.Interaction.642485b8afc547a4bab2df67 / 18646b8c31874540bef649717737e7cb | Partial: Python succeeded, second child still failed; coordinator honestly reported no verified result | 19.490 s | 10 / 29,001 | USD 0.047220 estimated |
| Readable HTML receipt, same two-task prompt | n.Interaction.843329856dca48b0a1075ce7 / 81fb885d05f04b32916de2c8b22da492 | Both tasks returned findings, quotes and links; delivered, closed/emitted | 30.704 s | 9 / 18,631 | USD 0.036320 estimated |

Final readback proves two researcher child runs, both completed, each quote matching
its own fetch receipts, and disjoint observed source IDs across tasks. UI shows
two delegate calls, search/fetch activity, both quoted findings and aggregated
statistics. All nine priced responses were retained; unknown cost count is zero.
The successful sources were Python's official essay and Pydantic AI's redirected
overview page. This is one successful repeated-specialist smoke, not a workload
quality/latency qualification or proof of claim entailment.

Fresh-install check caught a CI collection failure: native tests importing the
optional PageIndex service lacked PyPDF2. The CI lane now installs the tested
PageIndex-read and WebFetch dependencies directly from their Action manifests,
without growing the native core extra or importing the old LiteLLM test stack.
Clean Python 3.11.15 and 3.12.15 environments each pass 96 native/profile/WebFetch
cases and pip consistency checks (92 packages with the optional profiles). The
legacy evidence/fetch slice passes 51 cases. Remote CI remains pending; the
workflow runs on PRs/main, not this feature branch's push. No provider credentials
were needed for deterministic tests or copied into the acceptance record.

General answer/coordinator citation enforcement, semantic evaluations, interrupted
research rehydration, long-session context bounds, full skill/Action compatibility,
distributed ownership and migration/removal remain open. Research state is fresh
for each new Agent.run; durable continuation needs its own qualified evidence
rehydration policy. No enterprise qualification is claimed from these results.

## Library-owned working context and accounted summaries

Tested base `3a682a51` with the native context increment in the working tree.
`build_native_agent` now composes public `SummarizingCompaction` with defaults
32,000 estimated trigger tokens / 16,000 retained recent tokens
(`jvagent/harness/native.py:179`). These are distinct from the run's 100,000-token
usage budget. The Action persists both settings; children inherit them
(`jvagent/action/native_harness/native_harness_action.py:52`,
`jvagent/harness/native.py:218`). Configuration rejects nonpositive/noninteger
limits and a retention floor at or above the trigger.

JV does not implement compaction, a tokenizer, a summary prompt or a second
message store. The library preserves tool pairing, loaded capabilities, incremental
summary context and compaction receipts. The original first user message is not
pinned indefinitely. The same public graph persistence, spend capability and
request-scoped receipt binding are attached to the toolless library summarizer
(`jvagent/harness/native.py:130`). It uses the configured model, conversation and
shared UsageLimits. Unconditional descendant settlement includes summary receipts
even without configured delegates
(`jvagent/action/native_harness/native_harness_action.py:220`). Summary failures
propagate; there is no silent full-history fallback.

Deterministic regressions verify preserved facts/current input/host instructions,
summary child linkage and graph snapshots, shared conversation spend, recorded
summary cost, request-limit reservation, exhausted conversation budget, failed
summarization and no truncation of an oversized current message. The library
reserves one request for the answer: a request limit of one prevents the summary
request before it is sent. A deliberately tiny conversation budget records the
summary's spend and prevents the answer request. These tests use synthetic
FunctionModel responses; provider bill reconciliation is not inferred.

Actual GPT-4.1 browser test used the isolated app/graph on 8130/3130, with temporary
context limits 1,000/100 to trigger compaction within short UI-sized inputs.
Each input remained below the app's 2,000-character admission limit. The important
facts were code ORCHID-83 and rehearsal date 17 December 2026, alongside disposable
archive filler. Native events supply run duration below; usage is provider-token
measurement with library-estimated USD, not actual billed dollars.

| Browser scenario | Interaction / root run | Outcome and persisted state | Native run duration | Calls / tokens | Cost |
|---|---|---|---|---|---|
| Record two facts amid archive filler | n.Interaction.c7ff22e769284e0bb018762b / 5ac7383b74774db081daa7af1fa980ca | Both facts acknowledged; delivered, closed/emitted; no compaction | 1.187 s | 1 / 828 | USD 0.001806 estimated |
| Add filler, recall earlier facts | n.Interaction.7a340cecf6b6497389053f5a / f0693ca8ff664c8e87e14f46705604fa | Both facts correct; delivered, closed/emitted; one persisted CompactionPart and source filler absent | 2.907 s | 2 / 1,737 | USD 0.004326 estimated |
| Restart backend, reopen history, recall | n.Interaction.b325db6389f6476c9f570fb8 / eef7835962a04d50ab9604522a762d77 | Both facts correct after new backend process; delivered, closed/emitted; persisted summary retained | 1.241 s | 1 / 1,129 | USD 0.002402 estimated |

The second run links summarizer `01a1a972-a081-72b4-b4cd-83071f25582b` to the
root and caller conversation; both spend receipts are present. UI showed final
responses and live 828/1.7k/1.1k usage badges. No tool invocation was requested or
needed; no tool-activity qualification is claimed from this test. All three have
zero unknown-cost receipts. Reopening history restored responses, but historical
usage badges were absent: this remains a UI qualification gap.

Test-driver limitations were observed and corrected before that controlled test.
Typing a multiline fixture submitted overlapping requests: 19 matching graph
Interactions settled as delivered, while the UI also reported traversal errors.
They are not counted as a successful compaction benchmark or concurrency proof.
The fill helper then failed to update React's composer state and submitted a
leftover short input. A single-line 7,216-character fixture was rejected by the
app's configured 2,000-character limit before native execution. The client retried
alternate endpoint paths and displayed Not Found instead of the validation error
(`jvchat/src/config/api.ts:812`). That error-presentation/fallback behavior and
concurrent admission remain open findings. Controlled subsequent input used
single-line typing, explicit completion waits and graph readback.

The trigger is an estimate, and recent-token retention is a floor of whole
messages. Oversized current messages or indivisible tool pairs can exceed it;
there is no hard context-size guarantee. A summary is untrusted conversational
context, not fresh research provenance or authority. Summary semantic evaluations,
60-turn/provider-window tests, durable interrupted research rehydration, snapshot
retention, distributed fencing and engine migration/removal remain open. This
increment makes no performance or enterprise-readiness claim.

Final normal-default check restored graph/YAML context settings to 32,000/16,000
and restarted the disposable backend. Browser Interaction
`n.Interaction.572a30bc61f043ffbe72dc4b`, root
`c99a99a40d214bc681e54305db621b64`, delegated to reader and searched the internal
public Meridian document. It returned the fixture's 18 November 2026 / CEDAR-47,
showed delegate_task and pageindex__search activity plus a 3.9k token badge, and
settled delivered/closed/emitted: 7.342 s native duration, four model requests,
3,937 tokens, USD 0.007112 estimated, zero unknown-cost receipts. Its document-name
attribution is not claimed as enforced citation validation. The configured model
remains GPT-4.1. Clean Python 3.11/3.12 native/profile/WebFetch suites each pass
106 cases. The all-files commit gate is required again after formatter changes;
remote CI is still not implied by local tests.

## Browser request safety and persistent usage receipts

Tested base `0e9ef74c` plus this UI increment in the working tree, using the
isolated 8130/3130 app/graph and GPT-4.1. No backend execution loop was added or
changed. The native path still uses Pydantic AI/Harness. This closes two browser
qualification gaps discovered in the preceding context round.

The common interaction POST boundary now probes alternate prefix/host routes
only after an explicit HTTP 404 (`jvchat/src/config/api.ts:812`). Application
failures, unknown transport outcomes, JSON errors, stream read failures and
consumer callback exceptions propagate without resending a potentially executed
turn. HTTP validation detail is retained, including the server's nested error
shape. Both streaming and ordinary interaction entrypoints use this boundary.
Other generic client mutation APIs and server-side idempotency remain separate
qualification work; this is not a claim of exactly-once execution.

Replaced ad-hoc SSE splitting with pinned `eventsource-parser` 3.1.1, MIT, zero
transitive dependencies, compatible with the CI's Node 20 floor. Its bounded
8,388,608-character pending-event buffer fails visibly rather than growing indefinitely. Native session
tokens are still captured before forwarding the start event. Premature EOF
without a final/error event is reported, and an unterminated final event is
not dispatched. This follows WHATWG SSE section 9.2.6 rather than treating EOF
as a blank line:
https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation
The upstream API is documented at https://github.com/rexxars/eventsource-parser.
Npm lock comparison shows exactly one added package, no removals and no changed
existing package versions. Both lockfiles add only that parser. An initial
package-manager resolution attempted broad unrelated upgrades; those generated
changes were discarded and existing locked versions preserved before clean npm ci.

Usage persistence now projects a whitelist of finite nonnegative numeric totals
and bounded model-name/call rows, independently of the private debug payload
(`jvchat/src/lib/messageObservability.ts:31`,
`jvchat/src/utils/storage.ts:749`). It preserves unknown-cost counts and estimate
provenance through existing usage fields; a 128-row detail cap retains aggregate
totals and is disclosed in the footer. Prompts, tool payloads and arbitrary debug
fields are excluded from this receipt. Existing user-visible message/tool content
has its previous persistence behavior. Reopened messages use the cached receipt;
Debug stays unavailable when its full payload has been stripped. Already stripped
old histories are not backfilled, and cached browser data is not a billing ledger.

Validation evidence:

- Browser overlength probe (2,017 characters against a 2,000-character app limit)
  displayed the actual validation error. Instrumented browser fetch recorded
  exactly one POST, HTTP 422; graph Interaction count remained 52 before/after.
  No model call, run or cost receipt was created. Earlier Not Found masking and
  alternate-endpoint replay did not recur.
- Browser delegated lookup: Interaction
  `n.Interaction.88c8de59f0084c4893623231`, root
  `3fa79e5c3e694cb78cfa7ee9aa113250`, returned the public Meridian fixture's
  18 November 2026 / CEDAR-47. One interaction POST, HTTP 200; delegate_task and
  pageindex__search visible. Delivered, closed/emitted; native duration 6.781 s,
  four model calls, 4,060 provider-measured tokens, USD 0.007826 library-estimated,
  zero unknown-cost receipts. Browser reload/reopen retained both tool calls, all
  four model rows and Estimated $0.0078. Full debugData was absent from localStorage.
- Final pinned-parser bundle (`main-Dezn-PXi.js`) actual-model follow-up:
  Interaction `n.Interaction.3a71d9d2a4504490bfb3f8e8`, root
  `7c82104c56da4879a51f5f3b3590a6d5`, correctly recalled both facts without tools.
  Exactly one interaction POST, HTTP 200; delivered, closed/emitted; 1.635 s,
  one model call, 796 tokens, USD 0.001754 estimated, zero unknown-cost receipts.
  After reload/reopen, both the earlier 4.1k badge and new 796-token badge remained.
  The new cached receipt includes model_call_count=1 and no full debugData.

Fresh npm ci succeeds; full frontend lint, TypeScript/Vite build and 115 Vitest
cases pass on the normal Node 23.10 runtime. A second full UI test run with
explicit Node 20.20.2 also passes all 115 cases. The Python webui slice passes
eight cases. The bundled static client was rebuilt/staged with the repository
script and exercised in the browser. On a detached unmodified base with the new
regression cases, 12 of 20 targeted cases fail: duplicate transport/application
attempts, swallowed consumer errors, incomplete stream handling, unlimited
buffering and missing cached usage. The corrected checkout passes all of them.
This is mutation evidence for the fixes, not a runtime concurrency benchmark.

Open gates include server run ownership/reconciliation, safe retry with explicit
idempotency, ambiguous provider billing, native timing metrics (currently absent,
not guessed in the UI), historical receipt backfill, provider/channel/concurrency
qualification, migration and legacy engine removal. No enterprise qualification
or general performance improvement is claimed from this UI milestone.

## Accepted-input recovery for fresh user requests

Tested base `f3310283` plus the recovery increment in the working tree. A handled
native failure previously caused every immediate follow-up to raise before a new
model request. The native adapter now admits a new user request using the public
library's accepted-input checkpoint after inspecting the recorded run tree
(`jvagent/harness/recovery.py:18`). This is a host continuation policy for the
current read-only foreground profile, not another agent engine or automatic
retry/replay service.

Official StepPersistence now uses capture_frontier=True and versioned metadata,
with persistence ordered before compaction so even a summary failure can retain
accepted input (`jvagent/harness/native.py:150`). The helper uses public
inspect_recovery/is_provider_valid, and continue_run for delivered predecessors.
It verifies graph caller/conversation ownership, root registration/session,
terminal parent/child records, no unresolved effects and an initial complete
snapshot captured before the first recorded model request. Original input must
be present; a model response belonging to the failed run is forbidden in that
frontier. Actual emitted JV failure text may follow the input; no unacknowledged
answer or tool result is promoted into history. Usage settlement is idempotent,
and the new run records history_source without rewriting the old failure.

The graph StepStore exposes first_snapshot as a storage-only query of public
records (`jvagent/harness/step_store.py:107`). Remove it if upstream adds accepted
input selection to the public protocol. Run registration also rejects another
registration ID or changed scope instead of overwriting it; same-registration
replay preserves the initial record/timestamp. These are single-writer adapter
guards, not a distributed atomic-claim/fencing guarantee. No second database,
custom tool replay, synthetic tool returns or legacy execution fallback was added.

Unsafe predecessor admission now persists a blocked Interaction and displays a
recovery notice without a model call (`jvagent/action/native_harness/native_harness_action.py:179`).
A predecessor with empty native state is also rejected, so an unsuccessful
blocked attempt cannot bypass an older uncertainty by resetting history. Legacy
or mixed-engine histories require migration/reconciliation; older failed runs
without the marker are not retroactively declared safe. Operator tooling for
resolving a blocked chain remains open.

Deterministic regressions cover failure before response, paid-budget failure,
validation rejection with an undelivered answer present in the failure snapshot,
completed delegated children, fresh JSON graph reload, graceful provider-call
cancellation with unknown usage, changed caller/session/conversation, unqualified
old state, unfinished/mis-scoped children, unresolved effects, orphan roots,
late/interrupted/undelivered frontiers, empty/blocked predecessors and immutable
run registrations. Missing provider usage is persisted as unknown and blocks new
model execution until reconciled, including after graceful cancellation. Mutation tests in an isolated worktree removed unresolved-
effect, empty-predecessor and registration guards; all three corresponding
regressions failed for their intended assertions. Corrected checks pass.

Real GPT-4.1 browser evidence used the existing disposable app and graph on
8130/3130. Temporary run ceiling USD 0.002 deliberately exhausted a delegated
lookup; normal USD 2.00 was restored in YAML and graph before restarting the
backend. The test account reauthenticated and reopened the same conversation.
Durations below are native run-event durations, not end-to-end latency benchmarks.

| Scenario | Interaction / root run | Persisted and visible result | Duration | Model calls / tokens | Cost |
|---|---|---|---|---|---|
| Remember WILLOW-62, delegate internal lookup under tiny budget | n.Interaction.025c465bf45c47a1a72f7f38 / 1bcd39984ced4f77bcc807be2f29d7ad | Budget notice; delegate tool visible; failed, closed/emitted | 4.623 s | 2 / 1,868 | USD 0.004252 estimated |
| Restart, ask for original test code without repeating lookup | n.Interaction.66e9922ea58349058295ba7f / f902449a2ec545d98e8725177242b3d7 | WILLOW-62; no tools; delivered, closed/emitted; accepted_input source points to failed root | 0.945 s | 1 / 608 | USD 0.001258 estimated |
| Ordinary subsequent turn | n.Interaction.231adf472107433c8937b48c / 8f673fa490c645f6a50ecced48b18dc1 | 42; no tools; delivered, closed/emitted; delivered source points to preceding successful root | 1.236 s | 1 / 634 | USD 0.001286 estimated |

The failed tree includes reader 01a1a9a3-2329-7474-b107-39ba1a55a33d. Parent and
child both end with run_failed; the delegate effect is failed with an ended_at,
and no effect is left started. Its two measured provider receipts remain unchanged
after recovery. Across the sequence there are four measured model calls, 3,110
tokens and USD 0.006796 estimated, with zero unknown-cost receipts. No delegated
work was repeated on recovery. UI retained the failure tool item and 1.9k, 608
and 634-token badges. Actual provider-billed cost is not claimed.

The failed Interaction retains five snapshots across its two runs; each later
single-call run retains three. Capturing frontiers increases graph writes/storage;
this is recorded rather than claimed as a performance improvement. Retention,
long sessions and published deployment limits require further qualification.
This does not qualify in-flight process-death recovery, network partition/fencing,
external writes, bounded cancellation settlement under outage, signed-context
migration, or idempotent delivery. The old ingress HarnessRuntime coupling also
remains in the deletion inventory and must be retired. No enterprise claim is
made from this increment.

Final accounting-admission tightening requires zero known missing/unpriced cost
receipts before a failed/cancelled predecessor can supply history. This is
settlement of recorded logical model requests, not proof of final provider bills
or hidden SDK retry charges. A missing-receipt transport failure or graceful
cancellation stays unknown and blocks the next model request; it is never
reset to a free request. Two additional mutation tests removing this guard both
fail for their intended DID NOT RAISE assertions.

A final actual-model pair exercised the completed guard after new backend
processes: Interaction n.Interaction.d3741a8648664ef09300a490, run
a43bd70eb4f24c12af5aa844ee1bcf0e, deliberately exhausted USD 0.0001 while recording
ALDER-29. It failed visibly with a measured receipt: 1.953 s, one call, 526 tokens,
USD 0.001076 estimated, zero unknown-cost receipts. After restoring USD 2.00,
restarting, reauthenticating and reopening the conversation, Interaction
n.Interaction.5aa397d3868946d2946d2356, run bafc701ef71345a283348dd31a2dc9d6,
returned ALDER-29 without tools: delivered/closed/emitted, accepted_input provenance
to the failed root, 3.571 s, one call, 576 tokens, USD 0.001188 estimated and zero
unknown-cost receipts. Both token badges are visible. The app remains on normal
budgets and GPT-4.1.

Clean Python 3.11.15 and 3.12.15 qualification slices each pass 130 cases, including
23 recovery cases and the visible blocked-admission case. All-files formatting,
lint, type and secret checks pass before the final evidence-record append and
are rerun for the commit. Remote CI remains separate from these local gates.


## W4 foreground turn admission and deferred-storage correction

Active branch is now `feat/pydantic-harness-rebuild`, with Git author/committer
`eldonm <103538117+eldonm@users.noreply.github.com>`. Earlier branch names above
are historical. This increment is based on c77c0d4f; its tested source/test index
tree before this evidence append is ec6777eca0d903d821d3a6ce7c379df0c1eb976e.

Two new regressions failed against the preceding code: Interaction creation
occurred before the whole-turn lock, and a second request appended while the
first was executing. The HTTP explicit-Agent path now resolves the session
first, then creates, executes and finalizes under one conversation lock
(`jvagent/action/interact/interact_walker.py:561`). Denied/missing-memory/session
failure cases never execute. The compatibility initialize_interaction helper
remains separate and explicitly does not reserve an execution turn.

A real identity-cache regression also reproduced stale native_spend overwriting
prior receipts. Admission now uses public Conversation.find for persisted chain
counters and native_spend (`jvagent/memory/conversation.py:349`). Caller-supplied
interaction_limit behavior remains compatible; the existing Agent limit sync
still applies. Two redundant cached get calls are removed.

The first actual overlapping browser run exposed additional defects, rather than
qualifying the initial change. In conversation n.Conversation.79717af8c71f4982b81cb456,
the reader lookup succeeded (Interaction n.Interaction.79c4b4fb30c4469e9ebb77dc,
4 calls, 4,017 tokens, USD 0.007242 estimated). The queued model request also
produced BIRCH-84 (n.Interaction.dc2c4870244748eba4a0c160, 1 call, 742 tokens,
USD 0.001526 estimated), but jvchat displayed "Interaction was not created during
traversal." It remained unclosed, and the persisted conversation count/spend
excluded it. This is a failed smoke, not a passing completion claim.

Root causes and corrections:

- The old five-second bootstrap poll awaited the walker after its deadline, then
  unconditionally emitted a missing-interaction error. Streaming now polls while
  the walker is live and no Interaction exists; disconnect cancels it and queued
  waits emit 15-second heartbeats. A behavioral regression exceeds the old poll
  count and verifies start/final, closure and no error
  (`jvagent/action/interact/endpoints.py:978`).
- Conversation writes and native persistence adapters were relying on save()
  while the entity could still be in deferred batching. Admission now flushes
  its conversation before releasing the lock. Official step records, spend
  counters and native receipt-journal writes call public
  flush_deferred_entities(strict=True), ending deferred mode and propagating
  persistence failures. This prevents an earlier endpoint's late deferred flush
  overwriting a later admitted turn. Source anchors:
  `jvagent/harness/step_store.py:77`, `jvagent/harness/spend_store.py:99`,
  `jvagent/harness/accounting.py:90`, `jvagent/memory/conversation.py:400`.

Fresh-context step/spend tests now run with deferred batching both off and on;
a receipt-journal test reads persisted usage before the request's final flush.
Removing the three adapter flushes makes all three deferred-mode regressions
fail (missing persisted entities). Sources were restored before the final gates.
These findings qualify earlier "immediate persistence" evidence: that evidence
used deferred saves disabled and did not prove the default deferred path.

### Repeated actual-model browser evidence

Isolated disposable app/graph remains on 8130 with jvchat 3130 and GPT-4.1 through
OpenAI. Backend was restarted with the final runtime code. A new conversation
n.Conversation.3b6900f157d84dd3819abadb was seeded with MAPLE-92. Two independent
jvchat tabs then submitted a delegated lookup and a no-tool recall to that same
session. Access logs show overlapping POSTs at 23:31:34 and 23:31:35 local time;
the queued Interaction was created at 23:31:41.369686, exceeding the old five-second
admission interval. Both requests returned visible successful replies.

| Scenario | Interaction / root run | Result | Native run duration | Calls / tokens | Cost |
|---|---|---|---|---|---|
| Seed after restart | n.Interaction.ee79c832758e4a3891e9be32 / be352b18468d4863ac0ea6a456c898f9 | MAPLE-92 | 1.051 s | 1 / 533 | USD 0.001102 estimated |
| Delegated internal lookup | n.Interaction.63779b95756b4cdca82b1c90 / 98e418a1a78f44cb82bdfc3825f1c02b | 18 November 2026, CEDAR-47, named source; delegate_task and pageindex__search visible | 4.645 s | 4 / 3,938 | USD 0.008680 estimated |
| Concurrent queued recall | n.Interaction.da98ef39491b4b4f933816ea / c5959aba77784243b3d3c01b230d39ec | MAPLE-92; no error or tools | 0.813 s | 1 / 715 | USD 0.001466 estimated |

Readback asserts all three delivered/closed/emitted, conversation count three,
last ID equals queued recall, and history_source follows seed -> lookup -> recall.
The persisted conversation budget equals the exact receipt sum: six requests,
5,186 tokens, USD 0.011248 estimated, zero unpriced/unknown requests. Snapshots
number 3/12/3 respectively. UI badges display 533, 3.9k and 715 tokens. Durations
are root native run-event durations and exclude queued waiting and ingress;
they are not end-to-end latency benchmarks. Provider-billed actual dollars and
SDK internal attempt counts remain unqualified.

Temporary detailed browser/graph evidence is retained under
/private/tmp/jvagent-turn-admission-*. This record contains the durable conclusions.
Clean Python 3.11/3.12 qualification slices each pass 154 tests. The broader
interaction/memory slice passes 343, with two Redis multiprocess tests explicitly
skipped because JVAGENT_TEST_REDIS_URL is absent. All-files hooks pass after
formatting was rerun clean. No enterprise qualification is claimed.

Remaining concurrency gates include multi-worker fencing, PostgreSQL and Redis
fault tests, long queues/overload limits, programmatic Root traversal and separately
initialized walkers, admission-time policy revocation, background writers,
and bounded outage/cancellation settlement. Other Conversation fields and legacy
writers are not claimed concurrency-safe from this native foreground test.
Compatibility expansion, migration and legacy deletion remain required by W4/W5;
the 60-turn comparative workload and deployment gates remain incomplete.


A follow-up reload check exposed a separate jvchat limitation: each tab's cached
transcript contains its own submitted turn, but does not merge the other tab's
turn after reload/reselection. The graph retains all three Interactions and the
native history lineage includes the delegated turn. The two-tab test qualifies
foreground admission/accounting, not cross-tab client transcript convergence.
That client behavior remains an explicit browser gate and must be corrected.

Final repository-wide Python 3.10 compatibility run: 4,546 passed, 24 skipped
in 167.90 seconds. Native Harness is separately tested in the clean supported
3.11/3.12 environments above; skips in the legacy environment are not treated
as native qualification. After adding test annotations, the affected admission,
streaming and memory regression slice passes 21 tests. The three restored
deferred-persistence regressions pass again after mutation checking.


## Browser cache convergence after overlapping native turns

Based on 79fef49b, on feat/pydantic-harness-rebuild. The source/test index tree
before this evidence append is 6540cb8f8a7e8d3204180a45e99fe0eef7b05163.
The preceding round's confirmed cross-tab cache loss is corrected for new writes.
No backend execution, model policy, graph schema or provider dependency changed.

Each mounted chat view now tracks its own recent submitted/loaded snapshots.
A save applies that view's changed/removed messages to the latest stored cache;
unchanged stale messages cannot overwrite another tab's edits or resurrect a
removed branch. Cached presentation order is ignored when comparing removals.
Public Web Locks serialize the cache read/merge/write across tabs on supporting
browsers (`jvchat/src/lib/messageHistory.ts:48`). Baselines are capped at eight
recent sessions. Debug payload stripping and bounded numeric receipts reuse the
existing sanitizer. This is browser cache handling, not another agent state store.

SSE start binds the outgoing user bubble to the admitted Interaction, keeping its
reply/tool activity together when histories merge (`jvchat/src/hooks/useStreaming.ts:358`).
Independent tab order counters are normalized by turn, and outgoing user/fallback
IDs use crypto.randomUUID instead of millisecond timestamps (same file:277).
Explicit edit/resend remains an actual new model request and retains its existing
UI branch behavior. It does not erase the original graph Interaction or its spend.

Six new cache regressions cover disjoint stale writers, edited-away messages,
foreign edits, changed presentation order, Web Locks and immutable queued snapshots,
and debug-data exclusion. Replacing merge behavior with the original incoming
snapshot overwrite makes three of those tests fail; restoring it passes all six.
The first mutation attempt overlapped npm ci and could not find vitest; it was
not treated as evidence and was rerun after installation finished. Existing hook
fixtures now model actual cache read/write behavior, and assert user-to-Interaction
binding. All 121 UI tests pass on the current Node runtime and explicit Node
20.20.2. Clean npm ci, lint, TypeScript/Vite build and staged bundled-UI build pass;
Node 20 TypeScript/Vite build also passes. The Python interact slice passes 144.
All-files repository hooks pass and are rerun for the commit.

### Actual GPT-4.1 browser readback

The rebuilt bundled jvchat was served on 3130 against the existing isolated
8130 app/graph. This browser reports secureContext=true and Web Locks available.
Conversation n.Conversation.6307c4956ef44ea792d9bb0d / sess_47513a70c7464e3e:

| Scenario | Interaction / root run | Visible result | Native duration | Calls / tokens | Estimated USD |
|---|---|---|---|---|---|
| Seed shared code | n.Interaction.c0a04e1ba7ae42e4a8072b3e / f20d74a50b024a799a4f29c6ba587f31 | ASH-58 | 1.262 s | 1 / 532 | 0.001094 |
| First tab delegated lookup | n.Interaction.c1f319e6e10948f7bef7b838 / d4d47fa8b12f44f7bfef40a9713b45e1 | 18 November 2026, CEDAR-47, source; two tool calls | 6.337 s | 4 / 3,929 | 0.006910 |
| Overlapping second tab recall | n.Interaction.95360a8579624286b16ea6ac / db3b19d055ef406e959c03442621e5d7 | ASH-58 | 0.941 s | 1 / 712 | 0.001454 |
| Edit/resend recall to 41 plus 1 | n.Interaction.e92a27d3e4ec422ea4cdf4a2 / 185079476eff44cab8cff6b09998a7d9 | 42, branch selector works | 1.580 s | 1 / 738 | 0.001494 |
| Stale peer submits new follow-up | n.Interaction.f5a4804e707a4d7699cc1d05 / 309f56df049a4ce48db0107cdcd7ceff | CEDAR-47, no tools | 1.423 s | 1 / 767 | 0.001570 |

After the overlapping pair, both tabs were reloaded and reselected: all three
turns, tool activity and 532/3.9k/712 usage badges appeared in each. After the edit
and stale-peer save, both were reloaded again: four selected-view turns showed
ASH-58, the cited lookup, 42 and CEDAR-47; the old recall question did not return.
Usage badges were 532/3.9k/738/767. The cache contained no raw debugData, and the
Web Locks query showed no held/pending writes after settlement.

Graph readback retains all five Interactions, delivered/closed/emitted, with
history provenance following the real sequence including the original recall.
Conversation counters and spend equal their receipt sum: eight logical model
requests, 6,678 tokens, USD 0.012522 estimated, zero unknown/unpriced requests.
The original recall's cost remains counted although its UI branch is replaced.
Durations are root native event durations, not end-to-end timings or queue waits.
Provider-billed dollars/SDK attempt counts are not inferred. Detailed temporary
evidence is in /private/tmp/jvagent-history-browser-evidence.json and the two
jvagent-history-*-browser.txt snapshots.

Scope: this proves new-write convergence on reload with Web Locks and the tested
stale-edit sequence. It does not backfill messages lost by earlier builds, push
other tabs' changes live into an already rendered transcript, qualify cross-tab
writes without Web Locks, arbitrate simultaneous edits to the same message, or
qualify deletion while another tab streams. Cache quota/failure and browser-crash
behavior remain best effort; the graph is authoritative. Those browser gates,
harness compatibility/migration/deletion, outage recovery and comparative workload
evaluation remain open. No enterprise release claim is made.


## Bounded host cancellation receipt cleanup

Based on e60bcdee. Final source/test index before this record is
1c63bc763b6f753002b641ced9b06e811c0eaf16; native Action blob
6a2b97da96622c020ff6f18d81e2ab7fbec06ffa, accounting blob
2f346e32e8922eeeed950d94741c2a2dad6db9ad. Branch remains
feat/pydantic-harness-rebuild, author/committer eldonm.

Two behavioral regressions reproduced the previous unbounded cleanup: after
caller cancellation, a stalled graph save kept cleanup live past the test's
bound, with or without the writer suppressing cancellation. Tests release the
writer in finally so the red run itself cannot hang.

The host receipt helper now uses one monotonic five-second deadline across
usage normalization, save and strict deferred flush. Repeated caller cancellation
does not restart it. On expiry the write is cancelled without awaiting an
uncooperative cancellation acknowledgement. A strong-reference set retains and
observes delayed writes, capped at 16 in the qualified single-event-loop host;
capacity exhaustion does not start another cleanup write. Eventual failures are
consumed and logged without provider payloads. It returns a settlement result;
unconfirmed cleanup leaves the in-memory invocation blocked or delivery_unknown,
while preserving the caller's original cancellation. Sources:
`jvagent/action/native_harness/native_harness_action.py:16`, `:303`.

Usage normalization is inside that same bounded cleanup task. An unanswered
native request becomes provider_unreported, unknown-cost telemetry rather than
a zero-priced response. On a cancelled root it is classified as a cancelled
attempt (`jvagent/harness/accounting.py:204`). An early cancellation also flushes
an entity still in deferred mode. No provider retries or execution replay were
added, and no new database, dependency or public configuration surface was added.

Deterministic cases include repeated cancellation, cooperative/uncooperative
stalled writers, capacity exhaustion, eventual write failure observation,
deferred early persistence, failed settlement before/after delivery, and durable
unknown usage before another turn. Clean Python 3.11/3.12 native/profile/WebFetch
slices each pass 140 tests. The Python interact/memory slice passes 343, with two
real Redis multiprocess cases skipped because their service URL is not configured.
All-files hooks pass after restaging the isort change and rerunning clean.

### Real browser fault/cancellation evidence

Bundled jvchat 3130 and isolated API/graph 8130 were used with GPT-4.1/OpenAI.
An untracked temporary launcher stalled only Interaction.save for a cancellation
receipt. It did not change committed source or make the underlying JSON adapter
itself fail. This qualifies the host cleanup fault path, not every storage outage.
The launcher/flag were removed from active execution, and the ordinary CLI
backend was restarted for the final healthy-storage tests.

| Scenario | Interaction / root run | Persisted/visible outcome | Timing | Provider usage/cost |
|---|---|---|---|---|
| Initial ordinary seed | n.Interaction.da5daa3c78f74d84b303bd32 / 973c988f59664f639a4effd03ec30948 | PINE-63; delivered, closed/emitted; 533-token badge | 2.200 s native run | 1 receipt, 533 tokens, USD 0.001102 estimated |
| Preliminary injected cancellation, before usage-normalization addition | n.Interaction.4b98689c86ed4925857df6f0 / faad2c66b11d4170b8e2ae2755b7f775 | Stop ended UI generation; stalled final write cancelled; durable root remains running, no answer/ack | Stalled save 5.000 s | Provider stream opened; tokens/cost unknown; no terminal usage receipt persisted |
| Repeated injected cancellation after normalization addition | n.Interaction.9b697165deb346fa91208b41 / 9bf1fc35c3b446baac32d3f637d56123 | No answer/ack; durable root running because final receipt write failed | Stalled save 4.939 s within helper's shared 5 s budget | Provider stream opened; tokens/cost unknown; no terminal usage receipt persisted |
| Healthy-storage cancellation on final candidate | n.Interaction.5ef487a714a845af9a6c400d / 91c5623ed597415aa57fb8a6ca8e4f5f | cancelled, usage_complete=false; one cancelled attempt and one unknown-cost call persisted | Request-start event to normalized usage timestamp 5.850 s; no terminal run event | Provider stream opened; tokens/cost unknown, not free |
| Fresh conversation after restoring standard backend | n.Interaction.485aad42c85940a881e7d205 / ae5b8331e6ae433d906a753ecb7e6524 | RESTORE-PASS-96; delivered, closed/emitted; 530-token badge | 1.453 s native run | 1 receipt, 530 tokens, USD 0.001108 estimated |

For OAK the native Action blob matches the final candidate; accounting was the
e60bcdee version before the cancelled-attempt outcome label was added. Its write
failed before persisting usage. ELM and RESTORE used the final source/test tree
above. The preliminary PINE fault is retained as diagnostic evidence, not final
revision qualification.

Follow-up browser requests were visibly refused with the recovery notice and
zero new provider requests/events. PINE follow-up: n.Interaction.794383e3124e4964b8c67c67;
OAK after a fresh normal backend restart: n.Interaction.216a94ebf43447929dd6bc79;
ELM: n.Interaction.a4ea66204bb14771bd57647d. Each is persisted blocked, closed and
emitted with a predecessor reference. Their persisted Interaction-start to usage
finalization intervals were 2.233/2.516/0.916 s respectively; those are not full
HTTP latency benchmarks. Restarts and reauthentication did not bypass admission.

There were five observed logical provider requests in this round: two measured
receipts (1,063 tokens, USD 0.002210 estimated) plus three cancelled requests with
unknown token/cost outcomes. No total cost is claimed. The two failed final writes
retain request-start events but empty normalized usage dictionaries; that is
unsettled telemetry, not evidence of no charge. ELM durably marks the uncertainty.
Provider invoices and hidden SDK attempts remain unqualified. Detailed temporary
readback is /private/tmp/jvagent-cancel-browser-evidence.json; fault and restart
logs are retained separately under /private/tmp/jvagent-*cancel*.log.

### Remaining storage/recovery gate

This fixes the host receipt wait, not every operation in a cancelled run. The
pinned official StepPersistence on_run_error deliberately shields failure writes
without a deadline (`pydantic_ai_harness/step_persistence/_capability.py:562`).
Other ingress/graph operations, official failure-checkpoint writes, blocking
adapters, uncooperative-writer process shutdown, multi-loop/worker ownership,
provider bill reconciliation and operator recovery still require qualification.
The cancellation smoke retains nonterminal library request records; the host does
not invent run_failed events or automatically replay them. Operator tooling is
still missing. It is not claimed that graph writes can be forcibly interrupted.

The bounded task pool is a host storage-gap adapter, not an execution engine.
Replace it when a public jvspatial deadline/cancellation primitive supplies the
same bounded acknowledgement and late-outcome observation guarantees; preserve
fail-closed admission and unknown-cost accounting. No enterprise qualification,
complete outage recovery or performance improvement is claimed by this increment.


## PostgreSQL qualification and cache usage normalization — 2026-11-05

This increment qualifies a single-worker disposable PostgreSQL profile; it does
not close deployment, outage recovery, migration or simplification gates. Base
revision d1d47e96; final browser candidate source blobs: step_store
615b4df8834f50da55c7313a0cf9d0a144f3d603, accounting
dac8c8628cc989f5bef096a6235c0b8536b0317f and cost_estimator
a40f5d09203412e2713dc3e209e045df7c5800a0. The containing milestone commit
identifies the complete source and test tree.

### Confirmed defects and narrow fixes

A real table lock caused a PostgreSQL statement timeout after the Interaction's
cached native state had changed. Retrying registration/checkpoint storage could
acknowledge that cached duplicate without persisting it. Spend receipts had the
same defect. Duplicate registration/event acknowledgement now saves and strictly
flushes before returning (`jvagent/harness/step_store.py:32`, `:58`, `:72`;
`jvagent/harness/accounting.py:54`). These are storage retries, not automatic
model or effect re-execution. Added I/O on duplicate acknowledgement is deliberate;
no performance improvement is claimed.

The browser's first successful delegated lookup reported 1,152 cache-read tokens
in native snapshots but zero in normalized usage. The generic aggregation helper
now accepts the native receipt's cached_prompt_tokens/cache_write_tokens aliases
(`jvagent/action/model/cost_estimator.py:170`), and snapshot fallback includes
cache writes (`jvagent/harness/accounting.py:134`). Receipt provider labels use
the public RunContext.model.system property when available (`accounting.py:66`),
with unknown retained when unavailable. SDK spend amounts remain explicitly
estimated. This adds no pricing or execution engine. Existing persisted receipts
can be normalized on recomputation; this round performs no historical backfill.

### Deterministic qualification

- Python 3.11.15 and 3.12.15 fresh native environments, Pydantic AI core 2.66.0,
  official Harness 0.66.0, jvspatial 0.1.1, asyncpg 0.32.0; dependency checks pass.
- Each environment passed 151 native/declared-read-profile/WebFetch tests,
  including six real PostgreSQL tests. The six cover checkpoint/spend readback
  after pool/context reload and timed-out registration/event/keyed-snapshot/
  effect/receipt writes followed by a durable, single-record retry
  (`tests/harness/native/test_postgres_store.py:84`, `:131`). Each test creates
  and drops its own unique schema. Synthetic spend fixture prices are not bills.
- Fast regressions reproduce failed-save cache acknowledgement, then verify
  durability. The cache aggregation regression failed with cached count zero
  before the mapping fix; journal and snapshot paths now pass independently.
- Existing cost-estimator and usage tests: 38 passed. All-files hooks pass clean
  after formatter rerun. Full Python 3.10 legacy suite: 4,547 passed, 25 skipped,
  31 warnings in 156.64 s; the native SDK tests intentionally run in the separate
  supported Python 3.11/3.12 environments, not that legacy lane.
- Native CI now declares PostgreSQL 16 and the published driver's extra
  (`.github/workflows/test-jvagent.yaml:64`). Remote execution is not yet claimed.

### Actual-provider browser evidence

Isolated API 8131, bundled jvchat 3131, disposable PostgreSQL 16.14 container on
15452, no host database mount. Main graph uses PostgreSQL; logs and PageIndex use
separate disposable JSON stores. Agent n.Agent.cda3d608255a40fe984fd201,
conversation sess_5cb1105c0d24491d, GPT-4.1/OpenAI. All rows below durably read back
as delivered, closed/emitted and usage_complete=true. Successful transport does
not mean task-quality success. Durations are persisted Interaction start-to-close,
not browser wall-clock benchmarks. Native run durations are listed separately.

| Scenario / Interaction suffix | Visible result / quality | Interaction / native seconds | Logical model calls / tokens / estimated USD |
|---|---|---|---|
| Initial code memory, 23b174023f444e25a0086039 | **Failed semantic outcome:** SQL CHECK expression instead of HAZEL-38; 551-token badge | 2.828 / 1.766 | 1 / 551 / 0.001234 |
| Explicit clarification, ba5d6885bdbf4941b7902d54 | HAZEL-38; 596-token badge | 2.451 / 2.111 | 1 / 596 / 0.001234 |
| First delegated lookup, c17cd4a2bf1c4377aa4b9d92 | No documents found; delegation/search visible. Fixture documents belonged to the other agent collection | 6.230 / 5.427 | 4 / 3,988 / 0.008804 |
| Lookup after fixture registration, d093102a26e84a8783135dec | 18 November 2026, CEDAR-47, named source; two visible tool calls | 7.496 / 5.124 | 4 / 4,400 / 0.007978 |
| Recall after backend restart, 823a5d5a9ec64fd295271702 | HAZEL-38, 18 November 2026, CEDAR-47; no tools | 3.285 / 1.574 | 1 / 950 / 0.002020 |
| Final candidate after another restart, 9b1ad82427af4c45b3495ab6 | Correct date/label/source, delegate_task and pageindex__search visible; 4.8k-token badge | 8.793 / 6.940 | 4 / 4,836 / 0.008886 |

The first five rows preceded the final cache mapping/provider label addition;
the sixth used the final source blobs above. That final Interaction normalized
1,152 cached input tokens and all four receipts identify openai. Prior rows keep
their original stored usage/provider labels, including the original zero-cache
reporting defect. Overall observed receipts: 15 logical calls, 15,321 tokens,
USD 0.030156 estimated, matching the persisted conversation spend counter
(15 requests, 15,321 tokens); this is not invoice reconciliation or a success-rate
benchmark. Hidden SDK attempts and provider bills remain unqualified.

The collection miss is retained, not erased. The copied document roots referenced
the earlier JSON agent. The fixture was registered through public persist_structure
under the new agent's collection, root n.DocumentRootNode.a33c4edfc60d448494352b87,
with public access. Access filters were not weakened. The initial incorrect SQL
answer also remains a failed evaluation example; no intent classifier or server
extractor was introduced to conceal it.

Temporary readback: /private/tmp/jvagent-pg-browser-evidence.json. PostgreSQL test
logs: /private/tmp/jvagent-pg-full311.log and -full312.log. The final root run is
342661eca05b46e6aa6744d1793b8609; its reader child is
01a1aa30-ff5e-73ac-9968-2c97aa5e9d7c. Restart recall follows the preceding delivered
root through history_source, rather than resupplying expected values.

### Remaining limits

jvspatial's public command_timeout bounds individual statements, not pool.acquire,
connection reset or the full ingress/library failure-checkpoint path. Browser
requests during an injected real database outage have not been qualified by this
round; locked-store tests exercise the public adapter directly. Multiworker
ownership, pool saturation, abandoned-run recovery, external-effect replay,
operator tooling, upstream-main reconciliation, remote CI execution and the
60-turn baseline remain open. The native profile is still opt-in; legacy/pilot
execution and dependency conflicts remain until compatibility and migration gates
allow deletion. No enterprise-ready or faster-than-baseline claim is made.


## Reconcile current upstream and named-tool authorization — 2026-11-05

Merge parents: rebuild e34abedc and main 43cbc885 (seven previously missing main
commits). The six textual conflict files were resolved by preserving both Tool
metadata families, moving upstream named-tool helpers into shared host policy,
retaining hardened lease owner cancellation/deadlines, and keeping release notes.
Generic MCP transport lifecycle, OAuth token selection/expiry/cache invalidation,
handoff, WhatsApp and embed profile changes are retained. The containing merge
commit identifies the complete result. This is not a merge into main or a release.

Per the earlier user instruction that JV Agent must not know about Integral,
upstream's Integral OAuth/service-key modules, routes, defaults and corresponding
vendor tests are excluded. Generic injectable HTTP-client support remains. MCP
HTTP server auth profiles now reject explicitly instead of silently omitting auth
(`jvagent/action/mcp/mcp_action.py:428`). External providers belong outside this
library; native MCP remains an open compatibility gate.

### Host authorization and migration limits

Tool and decorator objects retain both effect_class and requires_tool_permission /
permission_denied_message (`jvagent/tooling/tool.py:32`,
`jvagent/tooling/tool_decorator.py:227`). Native dispatch checks the actual Tool
object through a host callback (`jvagent/harness/native.py:80`), passed unchanged
to children. The shared helper evaluates configured named-tool rules and the
Action gate; opted-in tools without enforcing policy/rule deny. Resolution errors
remain fail-closed with redacted structured events (`jvagent/tooling/access.py:53`).
Without a host Tool callback, the standalone factory denies explicit named-tool
permissions even if the legacy Action-label callback allows them. No new tool
scheduler or execution loop was introduced.

The new access-action / allowed-groups / denied-groups skill declarations are
rejected pending an adapter, rather than loaded without enforcing their meaning
(`jvagent/harness/skill_admission.py:39`). Handoff visitor bindings, external
messages and staff writes remain unqualified; preserving their modules does not
admit them to native execution. The machine-readable inventory adds an explicit
upstream delta while preserving the original 58-Action baseline.

### Tests and integration defects

Both Python 3.11/3.12 native + declared read profile + real PostgreSQL lanes pass
157 tests. Named policy tests cover Action/named grant and denial, absent explicit
policy, resolution/name/check failures and redacted logs. Native FunctionModel
tests prove actual callable suppression, including missing host callback. Decorator
collection retains both metadata families. Newly unsupported group declarations
have rejection regressions.

The first merge full run failed (154 failures, 4,537 passed): 153 traced to the
missing private compatibility export, plus an upstream test using asyncio.Barrier
on transitional Python 3.10. Re-export fixed, async policy call awaited, corresponding
pilot test doubles updated to AsyncMock, and the test's rendezvous now uses Event
without changing its two-worker-overlap assertion. Focused remaining suite passed
118 tests. The clean full rerun passed 4,692 tests, 25 skipped, 31 warnings in
162.55 s. Three subsequently added skill-admission cases pass in the 157-test
native lanes; a 56-test MCP/host-policy/admission slice also passes, including two
new HTTP auth-profile rejection cases. The push gate checks the final full tree. Clean all-files hooks are
required again after staging this evidence.

One concurrently loaded focused lease test missed its 0.6 s wall-clock threshold
at 0.608 s while still cancelling the owner. No production lease semantics or test
threshold was weakened. A subsequent 42-test policy/lease/decorator slice and the
full rerun passed. This records scheduling sensitivity, not a timing guarantee.

### Actual-model browser results

Disposable PostgreSQL app/graph 8131 + jvchat 3131, GPT-4.1/OpenAI; same isolated
fixture as prior round. All five requests below are retained, not selected only
for success. Each persisted delivered, closed/emitted, usage_complete=true.
Times are Interaction / native-run durations in seconds, not UI wall-clock SLAs.

| Interaction suffix / scenario | UI and persisted outcome | Seconds | Logical calls / tokens / estimated USD |
|---|---|---|---|
| 4f7e24b963f54094bd96c874 / misnamed policy fixture | Retrieved correct date/label; **not denial evidence**: loader skipped noncanonical jvagent/access_control | 7.049 / 5.366 | 4 / 3,964 / 0.007190 |
| 4330882dd4c64158b105dad6 / canonical stored deny | Permission denied, no facts disclosed; child snapshot returns Action access denied; two visible tool calls | 6.788 / 5.170 | 4 / 3,853 / 0.006914 |
| de7bfcb9e1fa4559b8f82545 / merge-mode allow attempt | Remained denied correctly: YAML merge retained stored deny; not positive-allow evidence | 7.383 / 5.003 | 4 / 3,882 / 0.007188 |
| 1dcfdd8bdabe4a209d8b9935 / explicit persisted allow | 18 November 2026, CEDAR-47, named/quoted Meridian launch public notes; delegate_task/pageindex__search visible | 7.877 / 6.239 | 4 / 4,020 / 0.007602 |
| bbe05778ce604e68a302eee4 / final-source restart recall | Correct date/label/document without tools; 789-token badge; prior delivered frontier loaded | 2.866 / 1.078 | 1 / 789 / 0.001800 |

Canonical AccessControlAction node a7cb1a3f9f404126a1fa6825 was verified in the
graph before the valid denial test. The allow transition replaced its policy
through the public entity save API, then restarted the backend; no access
filter was removed. This distinction matters: merge-mode YAML does not erase a
persisted deny list. The initial alias fixture and that merge attempt remain
recorded as setup failures, not harness security successes.

Final source blobs: native.py 51fe71b12cc07274474cc2049755232edf6d898a;
NativeHarnessAction 1d2b8edd4090e7996ba66728486cb16ed371489c;
shared access cb68a69b555ae1ebe47f593234ff066b2b76e05d;
skill_admission 592b0fbdc98868242846cc708e37fd635d5abb84. The first four requests
preceded the three explicit group-declaration admission guards; final recall ran
after reloading all final runtime sources. Temporary evidence is
/private/tmp/jvagent-upstream-browser-evidence.json. Receipts include all 17
logical calls, 16,508 tokens and USD 0.030694 estimated (four lookup runs each
report 1,152 cached input tokens). Costs are SDK price-table estimates, not bills;
transport retries/invoices remain open. Repeated snapshot copies are not counted
as extra model requests.

The permissive missing-policy behavior is an existing explicit deployment mode.
The skipped alias exposed a separate startup/migration limitation: missing declared
Actions currently warn and skip. A strict configured-policy admission gate remains
necessary before enterprise qualification; this fixture is not evidence that a
misspelled security Action fails closed at bootstrap. Other remaining W0/W3/W4/W5
and release gates are unchanged; main reconciliation does not qualify those.


## Remote qualification and transitional CI dependency repair — 2026-11-05

Draft [PR #260](https://github.com/TrueSelph/jvagent/pull/260) was opened at
8a99af19, mergeable against main 43cbc885. The
[first remote run](https://github.com/TrueSelph/jvagent/actions/runs/41977427036)
passed both native-harness Python 3.11/3.12 PostgreSQL lanes, jvchat, conformance
and the transitional two-worker lane. The separate pre-commit workflow passed.
Native 3.11's log has 157 passing dots, no skipped cases, and the configured
PostgreSQL service/DSN; these are real adapter tests, not page-load checks.
The two-worker result tests retained contracts, not native distributed ownership.

The full test/isolation/load jobs failed at collection because their `.[test]`
installation did not include pydantic_ai, imported directly by retained pilot
regressions and a stream-emission test. Those failures did not run model workflows.
The affected workflow installations now request `.[test,pydantic-pilot]` explicitly
(`.github/workflows/test-jvagent.yaml:35`), preserving the separately pinned native
lane. No production dependency was added, no tests were skipped to hide the error,
and the pilot extra is still due for removal with the old execution path at W5.
The full transitional job also runs pip check.

A new clean Python 3.11.15 environment installed that exact extra combination:
126 packages, jvspatial 0.1.1, pydantic-ai-slim 2.54.0, OpenAI 2.54.0 and
LiteLLM 1.101.6. Dependency check passed. Its full suite passed **4,697 tests,
25 skipped, 30 warnings in 134.36 s**, including all five cases added after the
prior full run. The native 2.66.0/Harness 0.66.0 environment stays independent;
these differing qualification profiles are not a final unified installation or
an equivalent-feature footprint/performance comparison.

Temporary install/full-test logs: /private/tmp/jvagent-legacy-ci-install.log and
/private/tmp/jvagent-legacy-ci-full.log. Remote failed collection output is retained
in /private/tmp/jvagent-native-ci-failed.log. This repair changes only workflow and
evidence files; the tested runtime sources and actual-model browser evidence from
8a99af19 are unchanged. The corrected remote workflow must still pass before the
entire PR check set is described as green. The PR remains draft and unmerged.


## Required Action admission and startup failure propagation — 2026-11-05

Base revision f90a3bd5. Declared Action lookup used to warn/skip missing or invalid
references, and a failed constructor could silently remove a declaration from
composition. Registration/agent installation failures could still report a usable
bootstrap. Separately, CLI/default embed startup ignored a false lifecycle result,
and failed graph enumeration could return an empty successful result map.

The existing ConfigError taxonomy now makes malformed/unresolved/duplicate Action
declarations, singleton conflicts and failed declared construction fatal
(`jvagent/action/loader/action_loader.py:1212`, `:1299`, `:1319`). Diagnostics use
entry position, not a dump of resolved configuration. Existing undeclared filesystem
discovery remains distinct. Every declared package must be valid, including a
currently disabled declaration; remove unused invalid declarations or fix/install
the package. No lenient fallback or new execution engine/config switch was added.

Action loading/construction precedes persisted Action cleanup in merge/source
updates (`jvagent/core/agent_loader.py:806`, `:820`). Reported registration failures
raise, failed declared agents abort app bootstrap before agent removal, and the
existing declarative CLI guard refuses to open the listener. Enumeration errors
propagate into the existing startup coordinator's failure result; CLI run/bootstrap
and default embed bootstrap now check it (`jvagent/core/app.py:311`,
`jvagent/cli/server_config.py:739`, `jvagent/cli/server.py:521`,
`jvagent/embed/bootstrap.py:163`). See the linked migration note in
[configuration.md](../../docs/configuration.md#action-startup-failures).

### Deterministic checks

Five initial loader regressions failed before implementation, proving silent skip,
wrong exception type and failed-construction acceptance. The final increment adds
13 cases for declaration/ambiguity, cleanup ordering, failed registration, all
three startup entrypoints and graph-enumeration failure. The missing-reference
cases also check that simulated private config values do not enter diagnostics.

A test-fixture-only caplog signature omission failed four focused cases while
adding log-redaction assertions; corrected without runtime changes. A formatter
rerun is required and completed before commit. The final clean Python 3.11 legacy
profile passed **4,710 tests, 25 skipped, 30 warnings in 130.14 s**. The native /
declared read profile / real PostgreSQL suites passed **157 tests each** on Python
3.11 and 3.12; no native SDK/provider ownership changed. The initial broader core
slice passed 238 and CLI/embed/startup slice 72 before the last focused additions.
All-files checks are rerun on staged final files; push/remote checks are separate.

### Disposable process and browser fault evidence

Same isolated PostgreSQL app 8131 + jvchat 3131, GPT-4.1/OpenAI. No user deployment
or graph was purged. Before faults: 11 Interactions, 32 measured model calls;
AccessControlAction n.AccessControlAction.a7cb1a3f9f404126a1fa6825, permissions SHA256
b52f7a345843703ba90b8cbf3097eb661f630e7a9baafa155b2f66aac19c6344.

- Change the sixth declaration from canonical jvagent/access_control_action to
  catalog shorthand jvagent/access_control. Merge-mode startup exits 1 at declared
  lookup; no listener/model turn starts. Browser submission visibly ends Failed to
  fetch. Graph counts and the policy ID/hash remain unchanged.
- A preliminary source command exits at its CLI confirmation (0.584 s); **not**
  admission evidence. The confirmed source-mode test against the same disposable
  graph exits 1 in 1.243 s at the declaration error, before Action reconciliation.
  The same counts/policy hash remain unchanged. The confirmation was not bypassed
  for a user deployment; the explicit source command targets only this fixture.
- Restore the canonical declaration and start merge mode. Real reader retrieval
  succeeds (first positive row below).
- Add a disposable local StartupFault Action whose on_startup raises the literal
  qualification startup fault. It registers, actually runs and fails; CLI exits 1
  in 1.667 s with Action initialization failed; startup refused, before listening.
  Counts remain 12 Interactions / 36 calls and the policy hash is unchanged. This
  proves failure propagation, **not** transactional registration rollback.
- Remove the fault declaration, merge-update the fixture and start normally.
  Restart recall succeeds. Final-source clean process restart and another recall
  succeed as well. Failed web submission created no persisted Interaction/model
  receipt. Source/merge fault logs and helper readback remain under
  /private/tmp/jvagent-strict-*.log and -before.json/-after.json.

| Interaction / root run | Visible and persisted outcome | Interaction / native seconds | Calls / tokens / estimated USD |
|---|---|---|---|
| 4eb339f2ee9545e7abc4c42e / f53224f93f8b49a7967e1c149acf32c6 | Reader retrieval: 18 November 2026, CEDAR-47, Meridian launch public notes; two tool calls and 4.0k badge | 8.141 / 6.549 | 4 / 3,987 / 0.007380 |
| 647bcb9b33664ac7a139558f / 7b216668f43f4ace9d98c2147bba5e3e | Correct date/label/document after lifecycle fault removed and backend restarted; no tools, 759-token badge | 3.989 / 2.186 | 1 / 759 / 0.001722 |
| b8210492d05040c9ac381f6b / 248f23108e4d42928b55e50adb0e1bad | Correct date/label/document on final-source normal restart; no tools, 816-token badge | 2.791 / 1.477 | 1 / 816 / 0.001836 |

All three are delivered, closed/emitted, usage_complete=true. Model receipts total
six logical calls, 5,562 tokens and USD 0.010938 estimated. First retrieval includes
1,152 cached input tokens. These are SDK price-table estimates, not provider bills
or a workload benchmark. History_source in the recall rows points to the previous
delivered native root. Durable readback is
/private/tmp/jvagent-strict-browser-evidence.json. The first two positive rows and
fault runs preceded final comment/dead-log cleanup; the third ran after reloading
all final runtime sources. Final runtime blobs: ActionLoader
fc936f9d98441c184c8b4c622783a25be4e3d5a8, AgentLoader
5e386093978f714fb31bcfff5bc77454fd237559, AppLoader
07f4951ffe331638fbf24bf934cb8616711557c2, App
934bef32cf0d12634d37eec33304f9de2e223696, CLI server_config
6830028c87da31739bff28b88f0c1122e53e5d40, CLI server
58b5e61773cc6868f543d820abc9509a93346fe2 and embed bootstrap
52c52a4586fe982e367b873c7b2c8c71a413dfb1. The containing commit identifies all tests/docs.

### Limits and next gates

This protects the tested process bootstrap, not a distributed/transactional
rollout. App/Agent/registration writes can precede a later failure; existing live
workers are not fenced by another worker's failed startup. Module reload failures,
runtime loss/removal of a policy node, dynamic installation and custom embed hosts
explicitly deferring initialization need separate qualification. A successfully
empty manual app is still permitted. Compatibility assets should use canonical
manifest names; structural validate alone is not package/runtime admission.

The native/Pydantic execution path, permissions and model/tool protocol are
unchanged. Remaining interview/script/MCP/write/proactive/channel, ownership,
migration, legacy deletion and baseline/release gates remain open. No enterprise
qualification or performance improvement is claimed by this increment.

## Native visitor-bound read compatibility — 2026-11-05

Candidate based on 986ddbe0 on `feat/pydantic-harness-rebuild`. Runtime source
blobs, per-run events, durations, usage, effects and raw-profile equality hashes
are retained in [browser evidence](2026-11-05-native-visitor-smoke.json). No graph
or user deployment was purged. Disposable PostgreSQL app 8131 and jvchat 3131
used actual OpenAI GPT-4.1 through core 2.66.0 / official Harness 0.66.0.

### Compatibility and confirmed findings

The native Action honors `binds_tools_to_visitor` on a copied Tool, preserving
existing Action metadata without mutating the original
(`jvagent/action/native_harness/native_harness_action.py:129`). The adapter
admits only declared nonterminal read tools, removes the host visitor argument
from the model schema, enforces access before injection, and uses official
Tool sequential scheduling (`jvagent/harness/native.py:75`). Frozen caller scope
is checked against the live visitor and Interaction before invocation
(`jvagent/tooling/context.py:50`). This is not a sandbox for untrusted Action code.

Only `leadgen__status` is newly qualified. Previously, status created or migrated
stored profiles. It now computes an unsaved view using current required fields
(`jvagent/action/leadgen/engine.py:424`). A separate isolated reproduction of the
previous committed lookup proved a misdirected User edge could return another
owner's profile. Conflicting ownership now fails closed
(`jvagent/action/leadgen/store.py:235`). Retrieve/capture/sync remain unqualified.

Import isolation initially failed because the tooling package eagerly imported
its legacy executor. Its compatibility export is now lazy; the native LeadGen
path does not import legacy/pilot execution, while the explicit old public import
still resolves (`jvagent/tooling/__init__.py:17`). This is an import separation,
not completion of legacy deletion.

The first browser delegation failed after one paid parent request because the
fixture used `lead_status` as its skill name. Official Skills rejects underscores
and skips invalid assets. The host now checks `Skills.load()`'s public catalog
for all configured root/child skills before any request
(`jvagent/harness/native.py:143`), without copying its validator or loader.
Renamed the disposable skill to `lead-status`; its independent delegate name
remains `lead_status`. Four regressions cover invalid names and non-invocable
root/child skills, asserting zero model requests. Catalog reads repeat during
library lifecycle; immutable assets/atomic configuration rollout are not proved.

### Actual browser and durable readback

| Interaction / root run | Outcome | Interaction / native seconds | Calls / tokens / estimated USD |
|---|---|---|---|
| d8a2374edf6a4d38aa2ae66a / fccedae3c19940688610be4379082e43 | Before fix: invalid child capability, visible configuration error; no status execution | 3.595 / 2.383 | 1 / 602 / 0.001492 |
| 5cdc3d9b4b97447f839ee1c1 / 003fe235a4de4320b5068c131af9947d | After fix, still invalid fixture: same visible configuration error before any model/tool execution | 0.968 / not started | 0 / 0 / 0 |
| 58e09bb397334fb2bc601f83 / 6656ecf9d1604f5fadff46eced069306 | Corrected fixture: email/full name required, email missing, 50% complete | 6.874 / 5.401 | 4 / 1,984 / 0.004766 |
| 1a881d6d855e433f90082b34 / acc56ede0480450bb476472ac03771ba | Explicit named-tool denial: no fields or score disclosed; model reports denied | 6.395 / 4.838 | 4 / 1,903 / 0.004520 |
| e1199a28f0c546aeb43ff212 / 96bb0e80c82548018f24008ec9f1aba8 | Original policy restored and backend restarted: correct email missing / 50% again | 6.845 / 5.291 | 4 / 2,008 / 0.004958 |

Positive/denied replies are delivered, closed/emitted, usage_complete=true;
UI shows delegate_task and leadgen__status and 2.0k/1.9k/2.0k token badges. The
before/after invalid-config failures are persisted failed and conservatively
usage_complete=false; the repaired failure has zero receipts and no SDK run.
Costs are price-table estimates, not provider-reported charges. The three
completed calls total 12 requests, 5,895 tokens and USD 0.014244 estimated.

Both fixture profiles compare exactly equal to their original raw database rows
after all five scenarios, including stored requirements/score, timestamps and
ownership. The second profile contains a different owner's complete profile;
status exposes only the authenticated caller's missing email. The named-tool
policy was restored exactly; normal backend remains available. This does not
substitute for an ordinary-member/browser account isolation test.

### Verification and remaining scope

Native + PageIndex/WebFetch + real PostgreSQL: 169 passed on each of Python 3.11
and 3.12. Scope mutation, missing context, model-supplied visitor, permission
denial, concurrent caller isolation, real graph identity, read-only profile
behavior and conflicting-edge ownership have regressions. Formatter changed one
new test; stage and rerun all hooks clean before commit. Full repository and final
hook results are recorded below once complete.

Remaining: other visitor Actions, interviews/scripts/continuation, MCP, writes,
external/proactive/channel effects, distributed ownership/recovery, migration,
legacy removal, unified dependencies and the 60-turn baseline/release gates.
No enterprise qualification or workload optimization is claimed.

Final local gates: fresh legacy Python 3.11 `[test,pydantic-pilot]` full suite
4,714 passed, 28 skipped, 30 warnings, 518.70 seconds. The added native preflight
module is skipped in that environment and executed in both native lanes above.
During the slow legacy run, process inspection observed a live connection to
127.0.0.1:11434; the test run was environment-dependent and does not qualify
hermetic network isolation. Actual browser scenarios used OpenAI, never local
models. This observation is retained as a test-infrastructure limitation.
All-files pre-commit passed clean after staging the formatter change. Secret
scanner false positives on revision/profile hashes were resolved by explicit
`git-commit:`, `git-blob:`, `sha256:` and `run:` identifiers in the evidence file;
no scanner rule was disabled. Push-time full tests and remote checks still need
separate results for the containing commit.


## Push-gate investigation and pricing correction — 2026-11-05

Do not treat the preceding 518.70-second full result as a reproducible gate: it
completed after interrupt input. The first push of 91ba0911 independently stalled;
the owned test process was diagnostically aborted with a stack dump (no core file,
no bypass), and the push did not occur. A location-only diagnostic run passed
4,714 / 28 in 112.69 seconds, demonstrating intermittent behavior rather than a
proved fix. The isolated lease tests passed 11 cases in 1.90 seconds.

A temporary socket guard then identified an actual unwanted network dependency:
`test_ollama_cloud_cost_uses_published_model_rates_but_local_stays_free` invoked
LiteLLM's synchronous runtime model metadata POST through the cost helper
(`jvagent/action/model/cost_estimator.py:206`). The guard failed the test rather
than silently substituting a provider response. The recorded call stack contains
frame locations only, not credentials or request content.

Added an adversarial regression with an unavailable metadata provider: local
GLM pricing incorrectly returned USD 5.80 per million input-plus-output tokens
from the cloud fallback, rather than zero. `pricing_for` now returns the existing
local-cost policy without any metadata probe; cloud IDs retain their explicitly
published table (`jvagent/action/model/cost_estimator.py:272`). This is a critical
transitional service correction, not an extension of the old execution engine.
The whole legacy estimator remains subject to the migration/deletion inventory.
The change does not enable or select local models.

A final real native browser recall after backend restart returned the correct
required fields, email missing and 50% score, with no tools. Interaction
40175b4283ff48e3a0f3de94 / run c61714636a504274bf5602e64e962af0 is delivered,
closed/emitted and usage_complete=true: one GPT-4.1 request, 740 tokens, USD
0.001624 estimated, 3.683 seconds interaction / 2.020 native. Its persisted
history points to the previous delivered root. See
[final recall evidence](2026-11-05-pricing-probe-smoke.json). This native smoke
checks unaffected execution/history/UI; the regression and socket-guarded suite
prove the legacy pricing behavior. Neither proves general network isolation.

The socket diagnostic and location tracer are disposable helpers outside the
repository; no production scheduler, test timeout bypass or dependency was added.
Final guarded/default gate results and the new push must be read separately.

The final socket-guarded full suite passed: 4,716 passed, 28 skipped, 30 warnings
in 113.92 seconds. No connection to Ollama's model port was attempted in that
run; the guard records and fails any attempt even if application code catches
the network exception. This is evidence for that endpoint/run, not a claim that
all external networking is blocked. Native sources are unchanged from their
169-case dual-version suites and the subsequent actual-model recall is above.
All-files hooks passed clean for the pricing correction. A normal push-hook run
without the diagnostic plugin remains required; remote release gates stay open.

## Native settings, complete-output boundary and workload calibration — 2026-11-05

The [detailed record](2026-11-05-native-settings-workload.md) is authoritative for
this increment's configuration, actual browser outcomes, source/dependency
controls, failed cases and limitations. It links a frozen 20-scenario protocol,
30-turn conversation sample, correlated UI/state receipts, reproducible fixture
setup/offline report tools and separate configuration/limit/recovery smokes.

Runtime additions are thin SDK adapters: attribute-backed common ModelSettings,
public-schema validation at startup/factory and propagation to children/summaries;
public before_output_process rejection of normalized length-limited final output;
compaction before request-start journaling to avoid charging a nonexistent parent
attempt after a failed summary. No custom continuation or execution loop is added.
The real clipped-output defect is retained before and after repair. Raising the
limit restores same-conversation accepted-input recovery, while an extra-period
format failure remains visible. Raw string output is not a typed format contract.

Final affected native/profile/PostgreSQL suites: 179 passed each on Python 3.11
and 3.12. The full legacy suite completed 4,725 passed / 29 skipped in 353.18 s
before the final completion guard; final native tests cover that guard. Reporter
semantic-scoring and unknown-cost hardening adds three tests; rerun the affected reporter slice and
all-files hooks before commit, and require the normal full push hook at the
containing revision. New remote checks must be read at that exact head.

The initial five-scenario sample remains 15/60 measured per implementation,
with 45 missing each. Mechanical checks: legacy 9 pass / 3 fail / 3 pending;
native 11 pass / 1 fail / 3 pending. All independent semantic review is pending.
Observed native token footprint is smaller for this slice; backend medians are
similar. This is not the workload acceptance or optimization gate. Source is
still larger during transition; legacy/pilot deletion, migration, other extension
classes, distributed recovery and release qualification remain mandatory.


## Native MCP read cohort and fast-failure delivery — 2026-11-05

See the [detailed record](2026-11-05-native-mcp.md), [graph receipts](2026-11-05-native-mcp-smoke.json)
and [UI observations](2026-11-05-native-mcp-ui.json). Native MCP now uses public
MCPToolset lifecycle, with an optional client extra, exact host-reviewed read
names and per-call caller/access checks. The rejected Action-client experiment,
its leaked processes and the original invisible admission notices are retained.
The SSE race is repaired by current-Interaction backlog replay, not a harness delay.
A public read-only retry-receipt gap adapter terminalizes the observed SDK retry
case without repeating tools or settling writes/cancellation.

Eleven browser attempts include eight actual-model turns: 16 requests, 7,152 tokens,
USD 0.016374 estimated, zero recorded unknown-cost requests. The final error has
a failed/end-dated tool record and confirmed subprocess exit. This is a functional
read cohort; MCP HTTP/auth/filesystem isolation/writes/delegation and broad release
qualification remain open. Interview SOP/state adaptation is still pending.


## Interview module hosting and cold PostgreSQL CI boundary — 2026-11-05

The [record](2026-11-05-interview-host.md) separates an import/declaration fix from
interview execution, which remains unqualified. A real GPT-4.1/MCP browser read
with InterviewAction installed succeeds (2 calls / 668 tokens / USD 0.001624
estimated), and the loop/executor stay unloaded. The process nevertheless loads
legacy HarnessRuntime through shared ingress/outbox hooks. This FAILS the stronger
native isolation gate; [host extraction](2026-11-05-shared-host-extraction.md) is
now the next removal dependency before enabling interview state writes.

Parent 749ccf28 CI passed eight checks but failed the native Python 3.11 cold-DDL
case under a 200ms fixture deadline. Normal setup/reload now has 10 seconds;
explicit locked-write cases keep 200ms and their original timing assertion. Both
local native/PostgreSQL lanes pass 195 tests; interview/import 283 and legacy
interview/loader/orchestrator 1201 pass with recorded skips/warnings. Fresh-head
remote CI remains required. No completed migration or enterprise claim is made.


## Native public identity binding — 2026-11-05

The [security and migration record](2026-11-05-public-identity-binding.md) documents
confirmed parent identity impersonation and anonymous session takeover, repaired
native admission, owner migration, ordinary-member browser API/logout checks and
real anonymous Messenger reload/recall. [Persisted receipts](2026-11-05-public-identity-smoke.json)
and [sanitized browser evidence](2026-11-05-public-identity-browser.json) include
actual GPT-4.1 calls and cost accounting. Public admission now relies on the
server-owned jvspatial authentication service and persisted Conversation ownership;
no execution engine or state database was added. Existing Messenger session-open
now sends its capability. The evidence record explicitly separates member API
coverage from authenticated UI coverage and notes popup tool-row rendering,
voice-provider, concurrency, timing and release limits. This advances W2/W3
security; W4 recovery, W5 migration/deletion and W6 qualification remain incomplete.


## Embedded cancellation — 2026-11-05

The [focused record](2026-11-05-embedded-cancellation.md) closes the basic embedded
host-disconnect gap through shared HTTP/embed teardown, SDK-owned terminal
persistence and registry cleanup. Repeated explicit cancellation no longer cuts
cleanup. The actual-provider jvchat host cohort shows MCP success, Stop with
unknown billing, blocked follow-up and a fresh ordinary HTTP success afterward.
Fresh PostgreSQL assertions and mutation checks cover the repaired failure.
Operator settlement, historical receipt migration, child/effect/deployment recovery
and the extension/migration/deletion release gates remain open.


## Interview platform-state prerequisite — 2026-11-05

The [dependency audit and repair](2026-11-05-interview-platform-state.md) proves
that interview scratch teardown could reopen a consumed signed host envelope.
Replay state is now retained as platform-owned data. Actual signed GPT-4.1 output,
real cleanup and still-unexpired replay rejection are recorded, with no replay
Interaction/provider call. Unit and fresh-PG cases cover batching on/off.
The linked course describes native SOP inheritance, activation, reviewed mutations
and task/session compatibility still to implement. This closes a prerequisite,
not full native interview execution or release qualification.

## Native SOP inheritance — 2026-11-05

The [native asset adapter](2026-11-05-native-sop-projection.md) preserves inherited
procedure text and cumulative prerequisite/tool policy through the public Skills
workspace boundary. Actual GPT-4.1 jvchat/MCP execution and fresh PG readback pass.
Missing/cyclic bases and inherited policy relaxation fail closed. Explicit native
interview activation, mutation effects, task compatibility and migration remain open.

## Native interview domain execution — 2026-11-05

[Evidence](2026-11-05-native-interview.md) covers explicit activation, validation,
review/confirmation across restart, reset/cancel and public SDK mutation receipts.
Failed/uncertain writes cannot disappear into pre-write accepted input. Legacy
task-close failures and unqualified callbacks fail closed; duplicate data.state is
retired on touched tasks. Final native/real-PG cohorts pass 280 tests on each of
Python 3.11 and 3.12; 282 existing interview tests pass. Seven actual-model browser
turns are delivered. Complex extensions, operator recovery, client reset, migration,
legacy deletion and enterprise release gates remain open. Parent 226c705f has all
nine exact-head CI checks green.
