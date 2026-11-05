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
| Dependency/Python | Pinned native pair installs/tests on Python 3.11 and 3.12; dependency checks pass | CI lane authored but remote run pending; artifact build; retire conflicting LiteLLM test dependency |
| Native execution | 75 native/profile cases; GPT-4.1 browser chat, follow-up, existing answer skill, research and failed-run usage | Repeated workloads and broader providers |
| Storage | Typed official snapshots survive fresh JSON context and abrupt worker exit | PostgreSQL, in-transaction death, outage and multi-worker fencing |
| Spend | Official capability, graph batch-store regressions, immediate spend journal and actual budget-failure browser receipts | Provider bill reconciliation, transport/SDK unknown receipts, historical-total migration and concurrency |
| Research | Existing evidence tests, native typed output and actual Serper/fetch/citation browser flow | Quality evaluation, publisher freshness and research efficiency |
| Compatibility | Named inventory, explicit admission, direct PageIndex and bounded foreground skill delegation with public retrieval and restricted-document denial | Interviews/flows, scripts, MCP, writes, proactive work, background/remote delegation, child crash recovery, general answer/delegation citation quality, channels |
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
