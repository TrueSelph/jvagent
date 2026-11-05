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
