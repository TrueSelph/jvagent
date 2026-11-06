# Native rebuild compatibility and qualification gates

[Machine-readable migration inventory](2026-11-04-harness-compatibility-inventory.json)
contains all 58 pre-rebuild Action manifests, 22 skill assets (including embedded
Action skills/examples), and 91 persisted Orchestrator settings with source lines.
The native_harness and pageindex_read Actions and delegate skill are additional
to that baseline. Dispositions are proposed engineering decisions; every integration still needs conformance proof.
No automatic migration tool or deletion authorization is inferred from a category.

## Qualification status

| Gate | Current evidence | Remaining work |
|---|---|---|
| Dependency/Python | Clean Python 3.11.15/3.12.15 installs, pinned native pair and declared read-tool profiles; 93 qualification packages, dependency checks pass | All nine remote checks passed at 6de0ff72; current increment requires its own remote checks; artifact build; retire conflicting LiteLLM test dependency |
| Native execution | 179 native/profile/WebFetch cases plus separate ingress/memory regressions; GPT-4.1 browser chat, follow-up, existing answer skill, research and failed-run usage | Repeated workloads and broader providers |
| Context | Official summarization, recent-tail retention, persisted compaction receipts and summary spend/request-limit regressions and GPT-4.1 browser recall after compaction/process restart | Long-session evaluation, context-window/provider matrix, oversized current requests and snapshot retention |
| Recovery admission | Public frontier capture and tree inspection, scoped input-only recovery, immutable registration, real GPT-4.1 budget failure/restart follow-ups; bounded host cancellation cleanup; actual cancelled-stream unknown usage and blocked follow-ups | Broader storage-I/O deadlines, abandoned-worker ownership, effect/write reconciliation, operator tooling, blocking-adapter and outage settlement |
| Storage | Typed official snapshots survive fresh JSON context and abrupt worker exit; step/spend/receipt writes verified with deferred batching both on and off | Single-worker PostgreSQL/checkpoint retry and restart readback now recorded; in-transaction death, outage and multi-worker fencing remain open |
| Startup configuration | Declared Action resolution/construction and registration failures refuse bootstrap; real misspelled-policy merge/source and failing-lifecycle process tests; existing policy preserved; restored GPT-4.1 browser delegation/recall | Atomic rollout, other live workers, runtime policy-node loss and custom hosts deferring initialization; strictness compatibility documented |
| Spend | Official capability, graph batch-store regressions, strict flushed journal, budget-failure receipts and two-tab serialized browser totals reconciled to six calls | Provider bill reconciliation, transport/SDK unknown receipts, historical-total migration and distributed concurrency |
| Research | Run-isolated evidence, mutation-checked reuse/concurrency regressions, canonical fetch excerpts and two-task GPT-4.1 browser success | Quality evaluation, publisher freshness and research efficiency |
| Compatibility | Named inventory, explicit admission, direct PageIndex, bounded foreground skill delegation, and reviewed visitor-bound LeadGen status with allow/deny/restart and unchanged-profile readback; plain-stdio MCP read success/error and cleanup | Interviews/flows, scripts, MCP HTTP/auth/filesystem scope/delegation/writes, proactive work, background/remote delegation, child crash recovery, general answer/delegation citation quality, channels |
| Browser client | Actual GPT-4.1 delegation/follow-up; two-tab reload convergence and stale-edit browser proof; tool activity/usage persist; one POST on validation failure; clean npm install and 121 UI tests | Live cross-tab updates and conflict/deletion cases, historical transcript/receipt backfill, cache failures, native timing, uncertain-outcome reconciliation, channel matrix |
| Simplification | Evidence code moved out of pilot | One execution path, old prompts/settings/providers removed after migration |
| Signed host policy | Administrator auth-required actual-provider success; nonce/server-run binding; nine UI and one nonstreaming validation rejections before execution; regression catches nonempty-policy downgrade; see [evidence](2026-11-05-native-host-context.md) | Member/foreign bearer/session, popup/actual-provider embed/cancellation, public-auth defaults/migration and distributed recovery remain open |
| Shared host | Native local admission/publication bypass legacy runtime; server correlation binds SDK/effects/reply; real browser success/error/restart cohort and module probe; deterministic signed-context, embed cancellation and fast-reply regressions | Actual-provider signed host/embed/popup/member/cancellation matrix; native durable cursor replay, distributed ownership, interview execution and final legacy deletion remain unqualified; see [results](2026-11-05-shared-host-results.md) |

Latest local scheduler/drain qualification: [timer record](2026-11-05-native-timer.md).
The normal two-minute timer dispatched actual native SDK/MCP work and delivered
its result through the browser. One SIGTERM with a parked reader exited in
30.672s, with unchanged persisted results/spending. This closes the local
single-worker closed-loop timer and idle-SSE drain cases only; active effects,
blocking shutdown hooks and distributed deployment remain open.

Offline migration global-flag integration is separately qualified in
[the guard record](2026-11-05-native-migration-flags.md): purge/update/source/merge
combinations refuse before global processing; four actual CLI invocations leave
the graph/source unchanged, followed by a successful real model/browser lookup.

Latest configuration migration qualification: [rehearsal](2026-11-05-native-migration.md).
Offline dry run/new-file candidate, explicit unknown-setting blockers, portable
skill paths and preserved public SDK pilot ceilings are implemented. A fresh
synthetic old declaration produces native/MCP/reply and actual model/tool/browser
success, with persisted configuration and usage proof. This is configuration-only:
the reference agent has 31 remaining blockers; stored graph migration, upgrade/
rollback, default cutover and implementation deletion are not complete.

## Required asset migrations

New-app execution default: [installed-wheel scaffold record](2026-11-05-native-scaffold.md).
Both creation CLI paths now default to the native profile with an official-loader
compatible primary skill. Actual installed-wheel jvchat recall and JsonDB usage
readback pass. Public first contact remains anonymous-capable; owner resume and
invalid bearer refusals are verified. Existing apps/graphs and explicit legacy
profiles are not migrated. Full native cutover and engine deletion remain open.

Native multi-skill runtime: [catalog record](2026-11-05-native-catalog.md).
Official deferred loading and JV active-skill/host-access checks pass actual
GPT-4.1 chat/MCP/research/greeting/research browser flows and fresh PostgreSQL
accounting. The initial research-to-greeting failure is retained and fixed through
current-run invocation scope. Scheduled catalog tasks have FunctionModel/real-PG
qualification; actual-provider timer execution remains open. This does not close
old selector/name/script/channel or persisted graph migration, nor legacy deletion.

Actual cumulative output-limit qualification: [budget record](2026-11-05-native-limit.md).
An OpenAI response crossing the SDK allowance retains a priced usage receipt
and failed outcome; an administrator allowance update permits explicit native/MCP
retry without restart. Reported tokens and estimated dollars reconcile in fresh
PostgreSQL, with no reserves. This does not qualify provider clipping, failure
notice replay after cache loss, invoice accuracy or distributed settlement.
Immediate public spend listeners currently force model streaming; retain the
supported callback until failure-safe delivery preserves the request protocol.

Latest background-channel qualification: [2026-11-05 record](2026-11-05-native-channel.md).
The local native jvchat foreground/scheduled-MCP profile now delivers unsolicited
replies, tool groups and aggregate usage, restores selected client history after
reload, and rejects revoked subscription capabilities. Native/host/real-PG suites
pass 333 cases on each Python 3.11/3.12; frontend passes 140 tests and build.
These incremental proofs supersede the earlier open live-background-client item
only for this profile. Durable server cursor replay, transparent credential recovery
after restart, cross-tab conflict cases and other channels/deployments remain open.

- Retain SKILL.md bodies, resources and scripts. Translate JV metadata through
  adapters; never assume the official loader enforces allowed-tools or Action
  prerequisites. Admission must reject unsupported semantics.
- Name/directory mismatches (for example pdf-generation vs pdf_generation) need
  naming migration or a scoped asset adapter. Script-backed and flow skills must
  retain their required tool/continuation behavior before becoming admissible.
- Preserve external Action imports and canonical manifest names where feasible.
  Move model configuration to native providers; provider-specific services such
  as embeddings, transcription and speech synthesis remain Actions.
- Before removing any persisted setting, implement and test its migration or emit
  an actionable rejection. The inventory flags every mapping as unimplemented.

## Workload and optimization acceptance

These are project acceptance decisions established before measurement, not
upstream guarantees. Use identical provider/model/settings, fixture data and task
sets for baseline and replacement. No local-model substitution is required.

1. Freeze 20 representative scenarios spanning chat/context, retrieval/research,
   artifacts, interview continuation, proactive work, delegation and recovery.
   Run each three times per implementation (60 turns), with a separate deterministic
   security/failure matrix. Provider prerequisites and costs must be recorded.
2. Require all deterministic security, permission and recovery invariants to pass;
   no duplicate externally committed effects in the supported effect matrix.
3. Require at least 95% successful provider-backed scenario runs, no unhandled
   empty terminal replies, and no quality regression against an explicitly scored
   baseline. Failures count in the denominator; report provider outages separately.
4. Target p95 end-to-end latency at most 1.2x baseline and median total model tokens
   at most 1.1x baseline on equivalent successful tasks. This permits measured
   validation overhead but does not permit masking failed or truncated outputs.
   Report sample size and uncertainty; 60 turns is a pilot gate, not an SLA.
5. Require no unaccounted provider attempt, explicit actual/estimated/unknown cost
   provenance, and a tested bound on in-flight budget overshoot. Official
   SpendLimits alone does not prove a hard spending ceiling.
6. Record source/dependency footprint before/after deletion. Do not trade safety,
   extension compatibility or quality for source-line reduction.

The [initial workload record](2026-11-05-native-settings-workload.md) now has 30
actual-provider browser turns: five conversation scenarios repeated three times
per implementation. It preserves three legacy exact-text failures, one native
JSON-format failure, pending semantic review and all request/token/cost totals.
It is a development sample with transport/source differences, not the full
60-turn-per-implementation gate or a qualified optimization comparison. The other
45 measurements per implementation and independent quality review remain open.
A production soak is a separate release gate.

## Transitional dependency conflict

The original combined `test,native-harness` profile was unsatisfiable because
test selected LiteLLM/OpenAI <3 and native selected OpenAI >=3.31; the pilot
also pinned an older Pydantic core. That original finding is preserved in the
older evidence. [Dependency qualification](2026-11-05-native-dependencies.md)
now aligns all Pydantic core extras to 2.66, removes LiteLLM from general tests,
and installs test/native/MCP/pilot compatibility together. Native CI runs the
full applicable suite on Python 3.11/3.12. LiteLLM remains an explicitly
incompatible extra and is qualified in the transitional CI profile.

Fresh editable and locked installs, full applicable suites and a runtime-only
wheel pass bounded checks. This does not yet unify the full legacy Action
manifest/requirements-all dependency set: LiteLLM retirement and native default
cutover still require migration/deletion. No forced overrides or opportunistic
LiteLLM upgrade are used.

## PostgreSQL profile increment — 2026-11-05

The [progress evidence](2026-11-04-harness-rebuild-progress.md) records six real
PostgreSQL tests, fresh Python 3.11/3.12 native lanes and a disposable actual-model
browser profile. Checkpoints/spend survive context reload; failed writes cannot
be acknowledged from cached duplicates (`jvagent/harness/step_store.py:58`,
`jvagent/harness/accounting.py:54`). Delegation, persisted history after backend
restart and native cache usage normalization have positive browser evidence.
One incorrect model answer and a cross-agent fixture collection miss are retained.
This does not qualify pool saturation, real outage browser recovery, multiworker
ownership, remote CI, legacy cutover or workload optimization. PostgreSQL service
configuration is added to the native CI lane; a configured job is not a passed job.


## Upstream and named-tool compatibility increment — 2026-11-05

Reconcile main 43cbc885 with rebuild e34abedc. Preserve upstream Action/domain
changes, lifecycle/OAuth fixes and explicit named-tool permission metadata;
exclude product-specific Integral auth in accordance with the user's prior
boundary. The inventory retains its original baseline and adds an upstream delta.
Native dispatch now applies Action and named-tool authorization through shared
host policy (`jvagent/tooling/access.py:53`), including delegated children
(`jvagent/harness/native.py:225`). Browser deny/allow/restart evidence is in the
progress record. New skill group declarations are explicitly rejected pending a
qualified adapter (`jvagent/harness/skill_admission.py:39`); this is a visible
compatibility limit, not completed handoff/native group-policy qualification.


The first PR #260 remote run passed native PostgreSQL lanes on both supported
Python versions and jvchat; transitional full/isolation/load jobs exposed a missing
optional pilot SDK. Their explicit `test,pydantic-pilot` profile now reproduces
4,697 passing tests in a fresh Python 3.11 environment with a clean dependency
check. See the progress record for run links and profile versions. Final unified
installation, cutover and deletion remain open; remote checks of the repair are
required separately.

## Visitor-bound read cohort — 2026-11-05

Partial compatibility is now evidenced for existing LeadGen `leadgen__status`
through official foreground delegation and native tool execution. The
[progress record](2026-11-04-harness-rebuild-progress.md) and
[machine evidence](2026-11-05-native-visitor-smoke.json) include the initial
invalid-skill failure, zero-request rejection after repair, positive and denied
actual GPT-4.1 browser runs, restored-policy restart and unchanged raw profiles.
Tests cover host-scope mutation, forged visitor arguments, access denial,
concurrent callers, real graph identity, profile mutation and conflicting legacy
ownership. Root/child skill invocability is checked through the official public
Skills catalog before model execution (`jvagent/harness/native.py:143`).

This qualifies one reviewed nonterminal read tool, not arbitrary visitor code,
flow continuations, LeadGen retrieve/capture/sync, ordinary-member browser access,
MCP or external effects. Native core/Harness still owns scheduling and execution;
JV supplies only the existing Action context and access boundary. Migration,
legacy deletion and the wider acceptance matrix remain incomplete.


## Model settings and completion boundary — 2026-11-05

The native Action persists common SDK ModelSettings, rejects invalid keys/types
on startup and applies them to roots, children and summaries. A real 16-token
Responses API test exposed clipped output being delivered as complete. A public
before_output_process capability now prevents that delivery, preserves its usage
and classifies output_token_limit. A same-conversation restart uses accepted input
without the clipped model text. Deterministic regressions cover text, summary and
output-function boundaries, including a false unknown parent attempt corrected
by placing compaction before request-start journaling. See the
[settings/workload record](2026-11-05-native-settings-workload.md).

Raw-string format failures remain. Typed output contracts, fresh-client-build
reproduction of observed 422/new-conversation issues, native request timings and
replacement of the deprecated on_spend shim remain open. None of these results
complete the provider, recovery, migration, deletion or enterprise release gates.


## MCP read and fast-failure increment

The [cohort record](2026-11-05-native-mcp.md) preserves the discarded client route,
MCP SDK pin correction, public MCPToolset replacement, server exit evidence,
SSE fast-reply race, read-retry receipt gap and actual provider/UI/state outcomes.
Only exact host-reviewed plain-stdio reads are admitted. Optional native-mcp
client dependencies preserve the smaller default native profile. Remaining MCP
classes, ordinary-member browser access, delivery recovery and release gates are
not qualified by these results.


## Interview hosting versus live native isolation

The [browser/process probe](2026-11-05-interview-host-smoke.json) confirms module
hosting without old loop/executor imports, but shared ingress and response
journaling still instantiate HarnessRuntime. Live native runtime isolation is
FAIL, despite cold-import and functional read success. Follow the
[shared-host extraction sequence](2026-11-05-shared-host-extraction.md) before
qualifying interview execution. Parent 749ccf28 has one native CI fixture timeout;
new-head CI is required after isolating normal setup from intentional fault limits.


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


## Native HTTP cancellation durability — 2026-11-05

The [focused acceptance record](2026-11-05-native-cancellation.md) closes the
confirmed HTTP teardown/durable-terminal gap and empty/interrupted false-zero
accounting. Actual Messenger Stop persists official SDK failure state and unknown
billing; a following request refuses another paid run, while a separate session
works. Fresh SQL assertions fail on the unshielded parent. In-memory tests alone
were insufficient. Historical receipt migration, operator recovery and all other
cancellation/effect/deployment classes remain open; no broader release or
simplification claim follows.


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

[Evidence](2026-11-05-native-sop-projection.md): inherited read procedure through
official Skills passes actual GPT-4.1 jvchat/MCP and fresh PostgreSQL readback.
Action bases require an explicit host map; the interview base, activation, mutations,
custom hooks and continuation remain unqualified. No migration/deletion gate closes.

## Native interview built-in vertical — 2026-11-05

[Record](2026-11-05-native-interview.md): built-in activation/validation/manual review
and later confirmation across restart pass actual GPT-4.1 browser and SQL checks.
Reset/cancel, write fault/cancellation and policy boundaries have deterministic
regressions. Native mutation intent uses official effect annotations. Complex hooks,
branching/for-each, external callbacks, delegated writes, operator recovery and
client reset remain unqualified; no release/migration/deletion gate closes.

## Client completed-stream transitions — 2026-11-05

[Actual browser/SQL evidence](2026-11-05-jvchat-reset.md) qualifies first-session
binding, completed-stream New Conversation, sidebar restoration and reload/selection
for the native fixture. In-flight transitions, late callback ownership and a fresh
packaged-wheel client remain unqualified. No harness migration/release gate closes.

## Client in-flight request ownership — 2026-11-05

[Record](2026-11-05-jvchat-ownership.md): actual-model New Conversation retires the
old request, keeps its sidebar entry and permits isolated new-session success.
Old uncertain usage refuses a paid continuation. Late callbacks/final cleanup,
Stop/sidebar overlap and attachment preparation have deterministic regressions.
Other live transition, lifecycle/telemetry, reconciliation and deployment gates remain open.

## Declarative branches and review binding — 2026-11-05

[Record](2026-11-05-native-branches.md): real GPT-4.1 browser intake, branch pivot
and confirmation after restart pass, with fresh SQL pruning/shared-field/task checks.
Invalid declarations fail admission. Corrected or missing review fingerprints cannot
authorize manual completion. Custom hooks/for-each, recovery, migration, deletion and
broader enterprise gates remain open.

## Native attempt and timing telemetry — 2026-11-05

[Evidence](2026-11-05-native-timing.md): official SDK model-request events now
populate attempts and measured duration; missing ends remain explicitly unmeasured.
Actual GPT-4.1 browser success/history and Stop have fresh PostgreSQL readback.
These are SDK boundary timings, not transport retry counts or pure decode latency.
Historical settlement/backfill, generic cancellation timestamps and broader
provider/recovery/migration/deletion/enterprise gates remain open.

## Admin uncertainty reserve — 2026-11-05

[Evidence](2026-11-05-native-reserve.md): admin-only, fact-bound estimates debit the
SDK run/conversation counters idempotently. Original unknown billing is retained.
Real GPT-4.1 continuation and member/anonymous denials pass with fresh PG readback.
This does not settle provider invoices, tool effects, uncertain delivery or abandoned
ownership. Blocked-notice chains, migration and broader release gates remain open.

## Qualified blocked-notice chains — 2026-11-05

[Record](2026-11-05-native-notices.md): up to 32 exact nonexecuting host notices can
supply user-role transcript history after original SDK admission succeeds. Real
GPT-4.1 recall through two blocked messages and backend restart passes, with fresh
snapshot/usage readback. Broken/cyclic/foreign/effectful notices fail closed. The
startup transport failure had no Interaction or charge; manual retry is explicit.
Other notice classes, effect/delivery/distributed recovery, migration, deletion and
release qualification remain open.

## Repeated item records — 2026-11-05

[Record](2026-11-05-native-repeats.md): explicit ID-scoped tools qualify built-in
repeated children, optional skips, validated future answers, insertion and corrections.
Fresh review binds child records. Four GPT-4.1 browser turns and a backend restart
reach persisted task completion; the initial scalar-argument failure is retained.
Native generic/staged bypasses and duplicate-record regressions are covered, including
real PostgreSQL with batching on/off. Custom hooks, nesting, park/resume, other
extension classes, migration, legacy deletion and enterprise release remain open.

## Scheduled tasks through native execution — 2026-11-05

[Record](2026-11-05-native-proactive.md): existing queue/claim services bind scheduled
domain input to the ordinary native Action. Actual GPT-4.1 browser requests prove
sibling-skill execution, MCP result, duplicate suppression, UTC eligibility after
restart and foreground recall. Task notices/uncertain outcomes cannot silently
complete or requeue. Initial closure failure and repaired timezone conversion are
recorded with adversarial regressions and fresh PostgreSQL outcomes. Background
jvchat synchronization/live tool display remains open; other triggers, callbacks,
channels, distributed recovery, migration, legacy deletion and release remain open.

## Completed native reply readback — 2026-11-05

[Actual provider/browser/SQL record](2026-11-05-native-replay.md) qualifies bounded
completed reply/tool/usage restoration after abrupt process restart and assistant
cache loss, with an empty new bus and no model/adapter replay or spending change.
Native/host/real-PG tests pass 356 on each Python 3.11/3.12; UI 140 and build pass.
This closes completed local native reply readback only. Arbitrary cursors, old
receipt migration, client acknowledgement, uncertain delivery, abandoned/distributed
runs, graceful shutdown, outage authentication and other deployments remain open.

## Browser outage authentication — 2026-11-05

[Actual expired-token/browser/provider/SQL record](2026-11-05-native-auth.md)
qualifies the local source reference-client profile: retain credentials on a
backend outage, fail before model admission, refresh after startup and continue
under the same caller/session. Frontend 159 tests/build and eight Python web-client
tests pass; all nine parent 37820122 CI checks passed. This closes only that local
outage-auth profile. Cross-tab rotation, outage load/Retry-After, clock skew,
per-message failures, packaged-wheel client and bounded shutdown remain open.

## Idle delivery renewal — 2026-11-05

[Real-expiry/browser/SDK/SQL record](2026-11-05-native-idle.md) proves read-only
renewal without another interaction POST or spending change, scheduled MCP
delivery afterward, and server revocation failing closed. Frontend 167 tests,
lint/build and eight web-client server tests pass. Parent 5acc988e jvchat CI failed
prefer-const; that declaration is fixed here and the manual lint gate is included.
The [production dependency audit](2026-11-05-native-ui-production-audit.json) has
five high entries; the full build/development audit adds critical Vitest/tinypool
entries. Both require exposure/mitigation evidence before release.
This does not qualify shutdown, timer deployment, cross-tab rotation, broader
providers/channels, packaging, migration or engine deletion.
