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
| Dependency/Python | Clean Python 3.11.15/3.12.15 installs, pinned native pair and declared read-tool profiles; 93 qualification packages, dependency checks pass | Native 3.11/3.12 PostgreSQL CI passed at 8a99af19; transitional job dependency repair pending remote rerun; artifact build; retire conflicting LiteLLM test dependency |
| Native execution | 157 native/profile/WebFetch cases plus separate ingress/memory regressions; GPT-4.1 browser chat, follow-up, existing answer skill, research and failed-run usage | Repeated workloads and broader providers |
| Context | Official summarization, recent-tail retention, persisted compaction receipts and summary spend/request-limit regressions and GPT-4.1 browser recall after compaction/process restart | Long-session evaluation, context-window/provider matrix, oversized current requests and snapshot retention |
| Recovery admission | Public frontier capture and tree inspection, scoped input-only recovery, immutable registration, real GPT-4.1 budget failure/restart follow-ups; bounded host cancellation cleanup; actual cancelled-stream unknown usage and blocked follow-ups | Broader storage-I/O deadlines, abandoned-worker ownership, effect/write reconciliation, operator tooling, blocking-adapter and outage settlement |
| Storage | Typed official snapshots survive fresh JSON context and abrupt worker exit; step/spend/receipt writes verified with deferred batching both on and off | Single-worker PostgreSQL/checkpoint retry and restart readback now recorded; in-transaction death, outage and multi-worker fencing remain open |
| Startup configuration | Declared Action resolution/construction and registration failures refuse bootstrap; real misspelled-policy merge/source and failing-lifecycle process tests; existing policy preserved; restored GPT-4.1 browser delegation/recall | Atomic rollout, other live workers, runtime policy-node loss and custom hosts deferring initialization; strictness compatibility documented |
| Spend | Official capability, graph batch-store regressions, strict flushed journal, budget-failure receipts and two-tab serialized browser totals reconciled to six calls | Provider bill reconciliation, transport/SDK unknown receipts, historical-total migration and distributed concurrency |
| Research | Run-isolated evidence, mutation-checked reuse/concurrency regressions, canonical fetch excerpts and two-task GPT-4.1 browser success | Quality evaluation, publisher freshness and research efficiency |
| Compatibility | Named inventory, explicit admission, direct PageIndex and bounded foreground skill delegation with public retrieval and restricted-document denial | Interviews/flows, scripts, MCP, writes, proactive work, background/remote delegation, child crash recovery, general answer/delegation citation quality, channels |
| Browser client | Actual GPT-4.1 delegation/follow-up; two-tab reload convergence and stale-edit browser proof; tool activity/usage persist; one POST on validation failure; clean npm install and 121 UI tests | Live cross-tab updates and conflict/deletion cases, historical transcript/receipt backfill, cache failures, native timing, uncertain-outcome reconciliation, channel matrix |
| Simplification | Evidence code moved out of pilot | One execution path, old prompts/settings/providers removed after migration |

## Required asset migrations

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

No workload numbers have been measured for this matrix. The updated OpenAI key now enables positive browser smoke tests, but the 60-turn
baseline/comparison has not run; initial smoke receipts do not fill that evidence gap. A production soak and larger performance
sample are separate release gates following this initial comparison.

## Transitional dependency conflict

A fresh resolver check proves `.[test,native-harness]` is unsatisfiable:
Pydantic AI core 2.66.0's OpenAI extra requires openai>=3.31.0, while the pinned
litellm>=1.101.0,<1.102 in the legacy test extra requires openai<3.0.0.
The pilot extra independently pins the older Pydantic core. Do not combine those
legacy extras with native-harness or claim a single qualified dependency graph.

The native CI lane uses `.[native-harness]` plus pytest/pytest-asyncio on Python
3.11 and 3.12. It does not qualify old model transports. Final cutover must remove
or isolate the obsolete transport/testing requirements and prove a clean unified
installation. Silently upgrading LiteLLM is not the intended engineering path.


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
