# JV Agent harness rebuild: execution contract

Status: implementation active; release qualification gates remain open.
See [the evidence record](2026-11-04-harness-rebuild-progress.md).
This refines [the reassessment](2026-11-04-pydantic-harness-transition.md).

## Decision

Build a new, small composition root around Pydantic AI core and official Harness
capabilities. Extract JV services behind adapters. Retire both the legacy loop and
the research-pilot execution wrapper. Do not incrementally generalize the pilot.
Use a temporary explicit development entrypoint during construction; ship one
execution path. The existing release remains the rollback artifact rather than
an indefinitely maintained second runtime in the new release.

## Distilled JV Agent product

**Declarations:** app.yaml/agent.yaml, Action info.yaml and canonical names,
attribute-backed configuration, SKILL.md content/resources/scripts, typed tool
schemas, identity and channel settings. Preserve user intent; translate or reject
obsolete execution directives explicitly.

**Modules:** Action packages, lifecycle hooks, dependency discovery, endpoints,
@tool and get_tools(), skill extensions, model/service integrations. Preserve
public extension imports where practical with thin forwarding aliases; aliases
must not import the old runtime.

**Services:** jvspatial App/Agent/User/Conversation graph, authentication and
AccessControl, storage, retrieval, task scheduling, response bus/channel adapters,
artifact handling, telemetry and accounting. Keep domain operations callable as
tools and services without embedding another model loop.

Source contract anchors: `jvagent/action/base.py:49`,
`jvagent/action/AGENTS.md:23`, `.planning/reference/action-authoring.md:59`,
`.planning/SPEC.md:26`. These existing contracts govern extraction; the following
module layout is proposed, not an assertion of implemented behavior.

## New composition boundary

Proposed `jvagent/harness/` contains only these responsibilities (combine files
when simpler; this is not a requirement to build a framework):

| Unit | Responsibility | Must not contain |
|---|---|---|
| factory | Build library Agent and selected capabilities from validated declarations | Custom think/act loop or skill router |
| context | Authenticated run dependencies, scoped graph/service references | Model-supplied caller identity |
| actions | Adapt JV Tool schemas/callables into public library toolsets | Parallel tool scheduler or JSON tool-call parser |
| skills | Translate JV metadata and provide skill assets to official Skills | Another skill interpreter |
| persistence | Implement public library storage contracts on jvspatial | A second database or bespoke replay engine |
| events | Map native events and final results to JV response protocol | Alternate provider streaming protocol |
| accounting | Normalize receipts and enforce shared spend policy | Provider message conversion or invented actual costs |
| compatibility | Explicit legacy config/import migrations | Silent fallbacks or generic delegation to legacy |

The InteractWalker remains ingress/lifecycle plumbing. The Orchestrator package
name can remain a compatibility facade whose execute method invokes the factory.
A model Action becomes configuration/service compatibility where a native provider
exists. Custom providers implement a documented public model interface. Embedding,
STT and TTS Actions remain separate service integrations.

## Keep, extract, retire ledger

| Existing surface | Decision |
|---|---|
| core/, action/loader/, Action base, plugin contracts, tooling decorators | Keep public contracts; remove incidental dependence on old orchestration |
| memory graph and TaskStore | Keep domain/session/task records; adapt native run persistence separately with explicit ownership |
| response/, channel Actions, ReplyAction | Keep transport and delivery policy; remove any second model generation from final publication |
| skills/ assets, skill_spec declarations | Keep authored assets; migrate execution-only metadata and task semantics |
| access.py, host_context.py, session_context.py | Extract only host identity/policy/context responsibilities |
| egress.py, uploads.py, proactive scheduling | Extract reusable services; task invocation enters the same new factory |
| loop.py, loop_helpers.py, legacy prompts.py | Retire after equivalent supported workflows pass |
| orchestrator_interact_action.py | Replace large execution implementation with thin compatibility facade |
| catalog/tools/core_tools/surface_policy/skill_gate | Retain schema/discovery/policy facts only; replace execution/routing with library interfaces |
| continuation/skill_tasks/task_runners/turn_state | Split domain task state from engine state; migrate the former, retire duplicate execution machinery |
| pilot/runtime.py | Retire after moving genuinely required custom-provider support to public adapters |
| pilot/contracts.py and evidence collector | Move reusable evidence contracts into research capability; discard research assumptions from generic runs |
| pilot/state.py and pilot/tools.py | Salvage tested policy/effect invariants; replace generic machinery with native contracts |
| old environment/YAML switches and prompt templates | Explicit migration/removal ledger; no ignored settings |

This is a disposition plan, not authorization to delete an entire mixed-purpose
module without tracing references. Each deletion must name replacement behavior,
callers, tests and persisted configuration impact.

## Prompt and skill model

Use library-managed instructions and skill discovery/loading. Supply minimal agent
identity, host policy and authenticated contextual facts. Keep task SOPs in skills;
keep domain functions in Actions. Existing tool names remain stable where possible.
Do not inject a legacy catalog, custom JSON action grammar, universal research
format, or a second planning loop into the core prompt. Research output validation
is opt-in through its capability. Untrusted tool/skill content cannot change host
identity, authorization or spending limits. Audit existing skill scripts separately
from enabling filesystem/shell capabilities; do not grant them ambient access.

## Work packages and deliverables

| Package | Depends on | Deliverables | Exit evidence |
|---|---|---|---|
| W0 Baseline | None | Enumerated shipped Actions/skills/settings/import contracts; dependency/version/Python compatibility matrix; deletion ledger; reproducible fixtures | Current supported workflows reproduced; baseline latency, usage and failures recorded |
| W1 Composition root | W0 | Qualified core/Harness dependencies, factory, typed context, native OpenAI provider, basic event/usage adapters | Real GPT-4.1 browser chat and non-research skill; full persisted usage readback; no old loop invocation |
| W2 Skills and Actions | W1 | Official skill loading plus JV metadata translation, Action toolsets, lifecycle and access integration | Existing research and artifact/retrieval skills; denied access, schema attacks, cancellation; extension fixtures unchanged or documented minor edits |
| W3 Persistence and effects | W1-W2 | Versioned native messages/steps in graph; task/run ownership; spend settlement; effect/delivery reconciliation | Crash injection at boundaries, restart, multi-worker ownership, duplicate delivery and unknown-outcome tests |
| W4 Remaining compatibility | W2-W3 | Interviews/flows, proactive execution, visitor-bound integrations, MCP, subagents | Each class passes browser/provider success and failure scenarios; nested budgets/access cannot expand |
| W5 Migration and removal | W4 | Config/data migration tool with dry run; compatibility facade; deleted legacy/pilot engines; docs/examples updated | Fresh install and upgrade; no old runtime imports; migration repeatability and rollback rehearsal |
| W6 Qualification/release | W5 | Provider/deployment matrix, regression/eval report, performance comparison and release notes | All required gates green; known limits explicit; milestone commit/push |

Keep implementation packages small enough to review and commit independently.
The earliest usable development build is W1; it is not the compatibility release.
Do not promise dates until W0 measures dependency and extension migration work.

## Required acceptance scenarios

- Ordinary conversation and multi-turn context; library-managed skill discovery,
  loading and switching; no universal evidence requirement.
- Research via Serper/fetch with evidence provenance; artifact generation and a
  retrieval skill with existing Action modules.
- Interview continuation, scheduled/proactive task and visitor-bound action.
- MCP and delegated subagent calls with inherited scope, shared limits and
  cancellation; explicit approval policy for external effects.
- Provider timeout, rate limit, malformed arguments, partial stream, disconnect,
  missing usage and exhausted budgets; every request reaches a defined state.
- Graph restart/outage, concurrent workers, abandoned tool call and response
  delivery uncertainty; no automatic duplicate external writes.
- jvchat/jvmessenger show responses, tool activity and usage, and restore history
  after reload; test channel delivery through actual configured adapters before
  claiming those channels qualified.

Every runtime increment uses a disposable app/graph and actual configured model
for in-browser smoke. Record revision, scenario, outcome, duration, visible events,
state readback and cost provenance. Do not substitute page-load checks for success
of a model workflow. Run deterministic/adversarial tests as separate gates.

## Optimization and completion

Measure identical task sets before/after: success rate, p50/p95 latency, provider
request count, prompt/output tokens, installed dependency footprint and maintained
harness code. Set workload-specific thresholds in W0; report uncertainty and sample
size. Do not claim faster execution merely because fewer source lines remain.

Completion means one Pydantic-owned runtime, all declared extension classes
qualified, obsolete machinery/configuration removed, migration tested, and required
security/recovery/browser/deployment gates evidenced. Preserve adversarial tests
by asserting behavior through the new adapters; delete only tests of retired
internal algorithms. Run staged-file-aware pre-commit and relevant/full pytest
before commits, and push validated milestones as already requested.

## First implementation batch

W0 followed by W1. Freeze feature additions to the legacy and pilot engines except
critical fixes. Use the existing branch until its state is reconciled; create a
`codex/` rebuild branch for implementation if isolation is useful. Do not change
running user deployments or migrate their data during planning. No separate agent
framework, orchestration abstraction or secondary state store is needed.
