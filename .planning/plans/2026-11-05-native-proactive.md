# Native scheduled-task execution — 2026-11-05

Parent `a007c2aad8868960e7681c8039b0dd670684a5bd`; all nine parent checks passed.
Branch `feat/pydantic-harness-rebuild`; author `eldonm`. This qualifies the recorded
local scheduled-task profile. Live background delivery into jvchat remains open.

## Execution ownership and preserved contracts

TaskMonitor retains the existing PROACTIVE TaskStore queue, eligibility and claim
services. For a native agent it passes a server-only binding of the claimed task,
caller, conversation and payload into the ordinary InteractWalker entrypoint.
Native admission revalidates scope and claim; client data cannot create that binding.
The user utterance remains empty. Only the actual scheduled objective/context enter
the SDK as clearly attributed task input, without promoting them to host policy
(`jvagent/core/task_dispatch.py:34`, `:81`, `:107`,
`jvagent/action/interact/interact_walker.py:406`).

The same NativeHarnessAction composition runs the task. An installed sibling skill
may be selected within the configured skill root; traversal and outward symlinks
are rejected. Normal skill prerequisites, tool permissions, effect classes and
shared run/conversation budgets remain binding. Pinned tools cannot expand the
skill allowlist. The binding is revalidated before SDK invocation, and task identity
is attached to the Interaction before execution (`jvagent/action/native_harness/native_harness_action.py:256`,
`:358`, `jvagent/core/task_dispatch.py:90`). No alternate model loop, task execution engine,
checkpoint store or state database was added. Legacy agents retain their existing
execution owner until migration.

Native task callbacks are rejected before claim/tick task mutation because external
webhook effects are unqualified. The interview staleness lookup admits only qualified
native specs. Explicit distributed ownership is still refused. Task claims are domain
scheduler state; public Pydantic interfaces own the model run, step/effect lifecycle,
spend and typed continuation history (`jvagent/core/task_dispatch.py:14`,
`jvagent/action/task_monitor/task_monitor.py:287`, `:518`).

## Outcome and clock repairs

Four regressions fail on the parent: blocked, failed, cancelled and delivery-unknown
notices could count as successful proactive output merely because response text was
nonempty. Native finalization now requires a delivered native receipt, nonempty
result, emitted response and closed Interaction. Errors, missing receipts and
uncertainty fail the domain task without automatically requeueing another paid run.
Cancellation finalizes under shielded host cleanup and propagates cancellation;
SDK execution records remain authoritative (`jvagent/action/task_monitor/finalize.py:99`,
`jvagent/action/task_monitor/task_monitor.py:586`).

The first actual browser dispatch returned the correct model/MCP result, but revealed
that programmatic dispatch bypassed HTTP Interaction closure. The task correctly
failed the new finalization check. TaskMonitor now closes and strictly persists its
Interaction before finalization, and strictly acknowledges the task mutation
(`jvagent/action/task_monitor/task_monitor.py:580`, `:610`). The original incomplete fixture is
retained; it was not rewritten into a successful task.

A second finding concerned scheduling instants: with no app timezone configured,
App.now() returns naïve server-local time, but app_now_aware_utc relabelled it UTC.
Two regressions fail on the parent for server/app Guyana time; configured UTC passes.
The helper now localizes according to the app/server clock and converts to UTC
(`jvagent/core/app.py:251`, `:582`). This is a shared scheduling-service fix, not a new
harness clock. An actual UTC-due task dispatch after restart proves the corrected
eligibility path. Earlier exported tick timestamps retain the original clock defect.

## Real GPT-4.1 browser and graph evidence

Disposable app/graph 8136, jvchat 3136. Browser admin fixture routes call the real
Agent.enqueue_proactive_task and TaskMonitor.tick; execution functions were not
patched. The fixture schedules synthetic reads only. A future-dated queued task
is activated by the fixture, with the final case using an explicit UTC timestamp.
Task callbacks are absent. [Browser requests/UI](2026-11-05-native-proactive-browser.json)
record outcomes, times, forgery rejection and duplicate ticks.

| Scenario | Outcome | Server seconds | Reported tokens | Estimated USD |
|---|---|---:|---:|---:|
| Initial scheduled lookup | SDK delivered; job failed because Interaction stayed open | Unmeasured closure | 829 | 0.001928 |
| Foreground READY | Delivered into jvchat | 3.313 | 1,555 | 0.003128 |
| Repaired scheduled lookup, sibling summary skill | MCP result Cedar / 14:30; task completed | 4.261 | 2,553 | 0.003648 |
| Foreground follow-up | Recalled scheduled result without another lookup | 2.626 | 2,762 | 0.003574 |
| Restart; explicitly UTC-due scheduled task | Dispatched, closed, completed | 3.253 | 2,455 | 0.005036 |

Full round: 10,154 provider-reported tokens across seven model requests,
USD 0.017314 estimated; no provider-reported dollar charge is asserted. The last
scheduled turn reused its existing SDK history and made no new MCP call. It proves
UTC eligibility/restart/task closure, not freshness enforcement or instruction
quality. The earlier repaired scheduled turn does contain the actual lookup receipt.
Both duplicate ticks dispatch zero additional work. Forged client task data with
empty utterance receives 422 before any new Interaction/provider attempt.

Fresh SQL exports: [queued](2026-11-05-native-proactive-queued-smoke.json),
[first closure failure](2026-11-05-native-proactive-first-smoke.json),
[repaired completion](2026-11-05-native-proactive-completed-smoke.json),
[foreground continuation](2026-11-05-native-proactive-final-smoke.json),
[UTC/restart final](2026-11-05-native-proactive-utc-final-smoke.json).
They retain runtime blobs, source revision, task states, SDK events/snapshots, usage
and response outcomes. Exports contain final typed snapshots and full record counts;
hashes have a sha256 display prefix, and private dispatch lease IDs are omitted.
The successful conversation has four delivered/closed/emitted Interactions and two
completed proactive tasks. The separate original task remains failed with its
unclosed Interaction. Final runtime blobs match the worktree; no legacy execution
modules are loaded.

## Checks and remaining gates

Native/profile/host/real-PostgreSQL cohorts: 333 pass on Python 3.11 (67.68s) and
3.12 (71.50s), 265 warnings each. They include SDK success, provider failure,
cancellation, unqualified tool pinning and real-PG outcome persistence with batching
on/off. Scope/claim/skill-root/client-forgery/callback/clock and affected monitor/host
tests pass 45 cases. All-file hooks pass after staging/formatting; publication must
also pass the normal full-suite push hook and new-head remote CI.

jvchat does not fetch unsolicited background responses into its local transcript:
reload showed only READY; a new foreground turn correctly recalled the scheduled
result and displayed usage. Background tool events were persisted but not shown as
live jvchat rows. This is an open live-delivery/client synchronization gate, not a
successful background chat-display claim. No frontend code was changed this round.

Event-trigger attachment, arbitrary task callbacks, external writes, other channels,
abandoned-worker settlement, distributed scheduling/fencing, repeated host cancellation,
long-duration jobs and failure/outage deployment profiles remain unqualified. Native
failed-task auto-retry semantics intentionally differ from legacy max_attempts;
operator reconciliation is required. Legacy queue/execution migration and deletion,
quality/freshness evaluation and the full enterprise release goal remain open.
