# Native skill continuation and required-data completion

## Decision and implementation

[ADR-0059](../adr/0059-native-skill-continuation.md) supersedes deterministic
turn routing for native execution. Official Harness skills/model messages own
selection; domain Actions retain validated session/task state. No extra router,
intent classifier, provider, executor, database or dependency is introduced.
The native interview SOP describes detours, authoritative domain results and a
status read when progress is unclear (`jvagent/action/interview/native_skill/SKILL.md:39`).

The offline planner explicitly retires typed true/false `lock_active_flow`,
including the implicit legacy true default. It preserves request/dollar policy
and requires review of the changed continuation behavior. Invalid boolean types
remain refused (`jvagent/cli/harness_migrate.py:78`). Retained legacy active tasks
still block state preflight; retiring a setting does not convert tasks or waive
host permissions (`tests/cli/test_harness_migrate.py:980`).

Review exposed a generic completion defect: both manual and auto interviews
could close with missing required fields after a review. Shared completion now
uses the existing current-path resolver before invoking a completion handler or
closing its task (`jvagent/action/interview/engine.py:2061`). Explicit allowed
skips and branch semantics remain that resolver's responsibility. The reference
signup handler loses its duplicate check and remains presentation/domain code;
its host-approved source digest is updated (`examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup-interview/scripts/custom_tools.py:1`).

## Actual-provider browser evidence

[Machine evidence](2026-11-06-native-continuation-smoke.json) binds parent
`7ded3a4b`, runtime source blobs, worker epochs, projected SOP hashes, graph
comparisons, request outcomes, durations and usage. [Browser snapshots](2026-11-06-native-continuation-browser.json)
retain visible replies, tools and statistics. This is an isolated PostgreSQL app
on backend 8177 and source jvchat 3140, using actual OpenAI GPT-4.1. Observational
profiling changes no runtime functions. Legacy execution modules remain unloaded.

The initial four-turn cohort collects contact email, loads a general arithmetic
skill for a detour, resumes after a terminal cold restart and completes on a
later confirmation. Session and task state remain exact across the detour and
restart. The resumed tool writes only the newly supplied preference.

One initial procedural expectation was **not met**: the model skipped the proposed
mandatory `get_status` read. Its domain writes/review still loaded the correct
persisted state. The SOP was refined to make status reads conditional on unclear
progress instead of adding a redundant RPC. Initial confirmation overlapped this
asset edit, so it is not final-source proof. Original acceptance and observations
are retained in the machine evidence rather than relabeled as passing that rule.

A fresh final-source four-turn cohort repeats start, arithmetic detour, terminal
cold restart, resumption/review and later explicit confirmation. Actual projected
SOP hashes match expected final documents on both workers. Detour changes neither
session nor task, all six pre-restart Interactions and both Conversations survive
exactly, and resume retains the original email/task ID without another start or
rewriting old answers. Manual completion closes that same task and clears session.

A separate final-source signup conversation collects validated name, normalized
Monday slot and email, explicitly skips optional phone, and shows review. Later
confirmation invokes the updated approved `signup_complete` exactly once. The
reply truthfully says the details are recorded locally with no booking or
notification. These two turns exercise the simplified handler through the shared
completion gate; they do not establish external delivery.

All ten observed turns deliver, each with one SDK terminal run and exactly one
terminal event per effect. All three tasks complete without duplicate `data.state`.
Reload and conversation selection restore replies/tools/statistics with all ten
Interactions and three Conversations exact, including spend. Initial records
remain unchanged throughout the later cohort. Thirty-two requests consume
71,718 provider-reported tokens; USD 0.080490 is **estimated**, not provider-billed
cost. Unknown-cost count is zero; conversation counters reconcile to requests.
The owned backend exits 143 after graceful shutdown, then its browser page closes.
The personal deployment/graph is not used.

## Regression and publication gates

Before the fixes, the three migration cases and two required-data cases fail.
Removing either protection again yields respectively three and two failures;
exact source bytes are restored. Strict invalid types, active retained tasks,
unchanged budgets and source preservation have regressions
(`tests/cli/test_harness_migrate.py:731`,
`tests/harness/native/test_interview.py:117`).

The affected interview/native-hook/branch/repeat/migration/state slices pass
**571 cases on each Python 3.11 and 3.12**. The first broad run passes 5,112,
skips 71 and fails only the stale tracked-asset inventory check, before its
required regeneration. After staging the new assets and regenerating the
inventory, all-file pre-commit hooks pass and the full transitional suite passes
**5,113 cases, 71 skipped, 31 warnings**. Its native SDK skips belong to the
separately installed native profile; they are not evidence of native coverage.
The complete native/host-context/PageIndex/web-fetch lanes, with real PostgreSQL
and the reviewed Docker script image enabled, separately pass **799 cases,
417 warnings on each Python 3.11 and 3.12**, with no skipped cases.

## Remaining scope

The [current offline reference report](2026-11-06-native-continuation-reference-blockers.json)
has three blockers: reviewed script image/resource policy, channel overrides and
unqualified model-service callers. Ten selected skill formats pass admission;
that is not whole-app runtime qualification or graph cutover.

Generic legacy IA-flow/task conversion, parked/abandoned policies, broader hook
classes, remote delegation, external delivery and distributed recovery remain
open. The legacy engine remains transitional pending supported workflow and
state cutover/deletion gates. This increment makes no performance improvement,
whole-app readiness or enterprise-release claim. The full rebuild goal remains active.
