# Pydantic AI-owned JV Agent: reassessment and transition

Date: 2026-11-04. Baseline: `7d689e4d`, `codex/pydantic-inspired-skill-pilot`.
Scope: architectural reassessment and migration plan; not fresh runtime qualification.

Execution details: [rebuild work packages and deletion ledger](2026-11-04-harness-rebuild-execution.md).

## Main goal

Make Pydantic AI core plus the official Pydantic AI Harness library the sole
execution foundation for JV Agent. Preserve modular Actions, existing SKILL.md
assets, object-spatial application state, identity/access policy and channel
interoperability through thin public-API adapters. Remove the superseded harness,
its prompt machinery, provider translations and settings after compatibility gates
pass. Require real browser/model evidence at every runtime milestone.

## Current position

- The dependency is still optional `pydantic-ai-slim==2.54.0`; the official Harness
  package is absent (`pyproject.toml:47`). This is a pilot adoption of core, not
  adoption of the current official Harness composition.
- Legacy execution remains the default and both drivers live in the Orchestrator
  (`jvagent/action/orchestrator/orchestrator_interact_action.py:514`, `:1248`).
- The pilot builds a real Pydantic Agent but restricts it to research evidence
  output (`jvagent/action/orchestrator/pilot/runtime.py:905`, `:986`). Its event
  loop consumes events without forwarding them directly (`:1024`).
- Model traffic still passes through a JV model translation layer
  (`jvagent/action/orchestrator/pilot/runtime.py:514`). Generalizing this layer
  indefinitely would preserve duplicate provider responsibilities.
- Action adaptation rechecks authorization and restricts effect classes
  (`jvagent/action/orchestrator/pilot/tools.py:239`, `:306`). Preserve these policy
  boundaries while replacing generic execution plumbing.
- Graph snapshots and delivery reconciliation exist
  (`jvagent/action/orchestrator/pilot/state.py:26`, `:267`, `:417`), but snapshots
  are not equivalent to exact engine replay or exactly-once external effects.
- General flows, MCP, write tools and delegation remain outside the pilot's
  published compatibility contract (`docs/pydantic-ai-pilot-compatibility.md:5`).

The original review and its updated dispositions remain the defect ledger:
[review](../reviews/2026-10-05-pydantic-ai-integration-review.md).
The [acceptance record](../reviews/2026-10-05-enterprise-harness-goal-progress.md)
records substantial security, evidence, accounting and cancellation work. Its late
entries and the compatibility matrix still describe unavailable provider success,
while the later usage-reporting milestone reported successful browser calls.
Reconcile those records before using them as release evidence. Earlier percentage
estimates are withdrawn: they mixed pilot hardening with platform migration.
No current production stability percentage is supportable from this inspection.

## Ownership and simplification

| Concern | Target owner | JV responsibility |
|---|---|---|
| Agent loop, messages, model settings, structured output, retries | Pydantic AI core | Config-to-public-API mapping |
| Provider protocol and usage receipts | Native Pydantic provider | Credentials/config and cost provenance; custom-provider adapter only when needed |
| Skills and context lifecycle | Official Harness capabilities | Translate JV metadata and scoped Action exposure; retain authored skill assets |
| Tool execution and delegation | Pydantic core/Harness | Action lifecycle, namespacing, caller policy and effect authorization |
| Run persistence/recovery | Public Pydantic persistence/durability interfaces | jvspatial-backed storage adapter; leases/fencing where required |
| Channels and final delivery | JV Agent | Response bus, ReplyAction, acknowledgment and reconciliation |
| Application memory and identity | JV Agent/jvspatial | Existing graph hierarchy, tenancy, access enforcement |
| Research evidence policy | Research skill/capability | Source receipts and claim validation, outside generic execution |

Do not install all optional capabilities. Start with skills and the capabilities
needed for an existing workflow. Shell, filesystem access and new durable engines
are not implicit requirements. A persistent step is not proof that an interrupted
external operation is safe to repeat.

## Dependency-ordered milestones

1. **Baseline and dependency qualification.** Inventory every Action/skill class,
   persisted setting and supported channel; classify preserve/adapt/retire. Select
   and pin a mutually compatible core/Harness pair by fresh install and public API
   probes. Reconcile review findings against tests. Record initial source size,
   dependency footprint, latency, token usage and failure rate. Gate: reproducible
   environment and a signed-off compatibility inventory with explicit exclusions.
2. **Native vertical slice.** Introduce one run factory using native provider,
   authenticated dependencies, official Skills, scoped Action toolsets and direct
   event translation. Keep prompts to identity, policy and task context; load skill
   instructions through the library. Move research validation into its capability.
   Gate: browser chat, research and a non-research existing skill; token/cost and
   tool events persist and remain visible after reload. No silent legacy fallback.
3. **Extension compatibility.** Adapt remaining SKILL.md metadata, interview flows,
   continuation, proactive tasks, visitor-bound Actions, MCP and subagents in named
   slices. Inventory unsupported metadata rather than dropping it. Child calls
   inherit caller scope and a shared spend ceiling. Gate: each class has normal,
   denial, cancellation and failure tests plus actual browser scenarios. Preserve
   extension contracts, not legacy execution algorithms.
4. **State and effects.** Implement the public step-store contract over jvspatial;
   retain full typed messages and versioned snapshots. Define process restart,
   ownership/fencing, cancellation deadlines and uncertain provider spend. Add
   explicit effect keys, durable intent/outcome and delivery reconciliation.
   Gate: crash tests before/during/after tools and delivery; multi-worker and
   PostgreSQL qualification; no unacknowledged automatic replay of unknown writes.
5. **Default cutover and deletion.** Migrate persisted configuration explicitly;
   switch all qualified workflows to the new run factory. Remove duplicate loops,
   prompt parsers, provider message conversion, research-specific generic branches,
   retired settings and unused dependencies. Preserve old data migration/readback.
   Gate: clean-install and upgrade tests, no reachable old harness path, reviewed
   deletion ledger and documented rollout/rollback boundary.
6. **Release qualification.** Run representative repeated browser workloads,
   provider failures, throttling, concurrency, long sessions, cancellation and
   recovery on the supported deployment matrix. Compare against baseline using
   predetermined workload-specific acceptance thresholds. Gate: regression suite,
   lint/types/secrets, quality evaluation, real-model browser receipts and explicit
   supported/unsupported matrix. Commit/push at validated milestones.

Each implementation milestone must record revision, provider/model, request and
run IDs, visible outcome/events, persisted state readback, elapsed time, token and
cost provenance, failures and untested boundaries. Unit tests and health checks
alone do not qualify a user workflow. Stop repeating identical failed provider
calls: resolve credentials/configuration once, or report that gate blocked.

## Upstream references and version caveat

Current official documentation was consulted on the assessment date. These are
candidate APIs for dependency qualification, not claims about the old pinned
2.54.0 installation:

- https://pydantic.dev/docs/ai/harness/ — official composable capabilities.
- https://pydantic.dev/docs/ai/harness/skills/ — on-demand SKILL.md loading.
- https://pydantic.dev/docs/ai/harness/step-persistence/ — settled-step storage.
- https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/ — durable execution and backend integration.

## Immediate next increment

Complete milestone 1, then build the native general-skill vertical slice. Do not
continue growing the research-only adapter into a second universal harness. The
prior active-goal record is paused and cannot be replaced through the available
goal API while unfinished; this file is the revised governing objective pending
that record's replacement through the goal controls.
