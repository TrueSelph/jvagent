# Official SDK planning with graph persistence — 2026-11-06

Parent revision: `74bc3b83fcb9ca9df85f62299c9a0edf76752fb1`; final runtime
blobs and both observed backend epochs are in the
[SQL/usage record](2026-11-06-native-planning-smoke.json). The first creation
precedes the additional explicit caller/graph-owner checks. Restart, update,
file compatibility, denial and isolation use the final runtime blobs. Tests
added afterward do not change those runtime sources.

## Ownership and replacement boundary

`NativeHarnessAction.planning: true` opts into the official Harness `Planning`
capability. The library owns instructions, tools and plan editing. JV supplies
the public six-method `PlanStore` adapter, backed by the existing Conversation;
no second planning loop, scheduler, database or dependency is added. Only SDK
`write_plan` and `read_plan` are exposed. The host capability ID `jv_planning`
avoids collision with a legitimate skill named `planning`. Sources:
`jvagent/action/native_harness/native_harness_action.py:110`,
`jvagent/harness/native.py:423`, `jvagent/harness/plan_store.py:11`.

The [native plan](../GLOSSARY.md#native-plan) is a checklist, not evidence that
its work happened, authorization to perform it, or a legacy TaskStore task.
This adapter delegates CRUD to public `InMemoryPlanStore`, using it only for
detached per-operation editing; graph state remains durable. It shares the
conversation spend lock, validates server-established user/session/agent scope,
checks actual graph ownership on first access, and strictly reads versioned
canonical items. Limits are 128 items, unique nonblank IDs up to 128 characters
and 64 KiB serialized items, with refusal rather than truncation. A missing ID
or unknown stored field requires migration instead of SDK parsing silently
generating IDs or dropping fields. Sources: `jvagent/harness/plan_store.py:19`,
`:42`, `:56`, `:81`; `jvagent/memory/conversation.py:119`, `:373`.

AccessControl checks named SDK tools independently of skill instructions and
checks read permission before plan context injection. Read-only execution hides
and rejects `write_plan`. Persistent writes require SDK step persistence and a
started `jv:mutation:plan:v1` effect record before the body runs. Failed or
uncertain writes therefore enter the existing mutation recovery barrier; this
increment does not authorize plan-specific operator reconciliation. Sources:
`jvagent/action/native_harness/native_harness_action.py:224`, `:376`;
`jvagent/harness/native.py:100`, `:240`, `:291`, `:423`.

## Browser and persistence evidence

Disposable PostgreSQL graph, backend 8153, source-built jvchat 3140, actual
`openai:gpt-4.1`. [Browser snapshot](2026-11-06-native-planning-browser.json)
and [SQL/SDK receipts](2026-11-06-native-planning-smoke.json) record:

1. Create a three-step workshop plan with explicit pending/in-progress states.
2. Graceful backend restart, read and update it while preserving all step IDs.
3. Write exactly `PLAN-ACTION-75` through the existing FileInterface Action;
   saved plan remains unchanged and no planned work is performed automatically.
4. Revoke the named `write_plan` grant through the existing Action API, then
   request all steps completed. Visible permission refusal; no write effect;
   saved statuses stay completed/in-progress/pending. Two real requests remain
   billed even though the host denied the tool.
5. Read the actual statuses after denial. Accepted-input continuation succeeds
   without treating the refused amendment as completed work.
6. Fresh conversation reports no plan; a further explicit `read_plan` request
   verifies that emptiness. Original plan is unchanged. The initial empty-plan
   answer used no tool; the subsequent verification did.
7. Clear only the original transcript cache and restore its sidebar thread.
   Four completed replies, tool groups and usage restore; seven Interactions,
   seven SDK registrations, plan/import/spend digests remain unchanged. Failed
   denial notices are outside this completed-pair projection.

Seven turns: six delivered, one expected permission refusal; 16 provider
requests, 23,490 provider-reported tokens, **$0.026664 estimated** from SDK
pricing, zero unknown-cost calls. These are not provider invoice costs. Each
Interaction records elapsed time, request timings, receipts and final outcome;
receipts reconcile with both conversation spending counters. Both Conversations
retain empty legacy task sets. The explicit permission grant is restored.
The observer records no legacy execution modules or patched execution functions.

## Migration and verification

The offline converter strictly maps boolean `planning` into the native
capability. The actual fixture starts from a legacy Orchestrator YAML, converts
before bootstrap, and preserves its three modular Actions. Existing stored
legacy plans/tasks and old `plan`/`update_plan` instructions are not converted
or silently aliased. Source: `jvagent/cli/harness_migrate.py:42`, `:287`.
The [current reference-app report](2026-11-06-native-planning-reference-blockers.json)
has **17 blockers**, down from 18; the reference app still cannot cut over.

- Python 3.11: 508 native/host/real-PostgreSQL tests pass, 111.15 seconds.
- Python 3.12: same 508 pass, 106.86 seconds.
- Native CLI migration: 75 pass. Transitional memory: 200 pass, two Redis
  environment skips. The transitional environment lacks the official Harness;
  CLI migration is tested separately in the native environment.
- Final planning slice: 16 pass, including strict-flush failure, provider failure
  after mutation, read-only/denied tools, wrong caller/agent, invalid stored
  schema and SDK capability-name collision. Sources:
  `tests/harness/native/test_planning.py:59`, `:104`, `:137`, `:255`, `:275`;
  `tests/cli/test_harness_migrate.py`.
- A detached mutation worktree removing stored-item/version guards fails all
  three corresponding malformed-schema cases. The default SDK parser would
  otherwise generate missing IDs or discard unknown fields. Private raw log:
  `/private/tmp/jv-plan-schema-mutation.txt`; this deliberate mutation is not
  applied to the implementation branch.

## Remaining gates

No retained legacy plan/task import, plan-specific operator reconciliation,
actual crash-mid-plan cohort, child/background/distributed planning, granular
SDK subtask tools, distributed fencing or live reparenting qualification is
claimed. Owner verification is cached per adapter/root run. Strict flush failure
does not imply database rollback; uncertain writes remain blocked from replay.
Same-user conversation isolation is live-tested; wrong user/agent is deterministic
coverage, not a new ordinary-member live cohort. The default stays disabled.

Legacy planner code remains until all its callers/state qualify for removal.
The removal ledger replaces generic planning with the official capability;
`planning_prompt` and `planning_heavy_first_tick` are separate obsolete-policy
decisions, not restored legacy settings. No performance improvement, complete
reference-app migration, engine deletion or enterprise qualification is claimed.
