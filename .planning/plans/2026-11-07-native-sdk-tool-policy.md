# SDK execution deadline policy qualification

Parent revision: `915854e5`. This increment maps legacy global/channel execution
deadlines into public SDK policy, retaining modular Actions, official Skills,
reviewed MCP and existing graph receipts. It introduces no timer, dispatcher,
provider protocol, state database or execution engine.

## Ownership and clock contract

- Optional `tool_timeout_seconds` is positive and finite. Exact-channel profiles
  may tighten it; null, zero, malformed or widening overrides fail admission,
  including unmatched profiles. Run arguments remain separate. Sources:
  `jvagent/harness/run_limits.py:17`, `:52`, `:79`.
- JV Action tools use public `Tool.timeout`; function definitions for planning,
  scripts and foreground delegation use the same or a tighter existing value.
  Children inherit the selected cap. Sources:
  `jvagent/harness/native.py:128`, `:321`, `:634`.
- Reviewed MCP uses the actual public FastMCP `Client(timeout=...)`, selecting
  the smaller service/host RPC limit. Initialization retains `init_timeout`.
  Unknown toolsets cannot ignore configured policy. Sources:
  `jvagent/action/mcp/mcp_action.py:960`, `jvagent/harness/native.py:76`.
- SDK capability-loading/output controls remain run-bound; they do not carry a
  fabricated function-body timeout. The SDK control-loader cancellation
  regression exercises public `agent.run(timeout=...)`. These clocks do not
  reproduce a legacy end-to-end wrapper lifetime or hard-preempt blocking Python.
  Source: `tests/harness/native/test_tool_deadlines.py:364`.
- Host identity and selected policy are captured before integration providers;
  selected policy persists in success/failure/cancel receipts. No-keyword
  provider compatibility remains when the optional policy is unset. Sources:
  `jvagent/action/native_harness/native_harness_action.py:300`, `:329`, `:591`.

## Effects and confirmed review gaps

An annotated mutation timeout cannot authorize another model attempt or queued
write. Its effect remains uncertain and later turns require reconciliation.
For a known `NativeMutationTool`, exactly unannotated `effect_summary=None`
proves the persisted intent fence was not crossed; SDK retry failure may close
that pre-intent receipt. Arbitrary failure text is not rollback evidence.
Identity/recovery-scope methods must not mutate before the fence. Sources:
`jvagent/harness/read_retries.py:54`, `:64`,
`jvagent/harness/mutation_tools.py:52`.

SDK foreground child cancellation also exposed a parent read receipt left
started. The narrow public error-hook adapter now covers reviewed read-only
delegation and plan reads. Cancellation of annotated scripts still stops with
unknown effects. This extends the existing pinned upstream retry-receipt gap
adapter; remove repair when upstream terminalizes these retry paths. SDK clocks,
dispatch and execution persistence remain authoritative. Sources:
`jvagent/harness/native.py:468`, `tests/harness/native/test_delegation.py:28`,
`tests/harness/native/test_script_sandbox.py:27`.

Mutation checks deliberately remove the MCP client clock while retaining its
declared marker: both real stdio regressions fail because the slow RPC succeeds.
Removing pre-intent repair fails with a started receipt instead of failed.
Sources are restored byte-for-byte, followed by the affected suite. Earlier
before-fix child cancellation also reproduces the started parent receipt.
See [test evidence](2026-11-07-native-sdk-tool-policy-tests.json).

## Migration boundary

The public offline CLI proposes positive finite `tool_call_timeout` as native
`tool_timeout_seconds`, globally or as a tightening channel policy. Numeric zero
retires only an inactive clock; channel zero cannot disable an active base.
Reports require review of function-body/RPC, initialization/run and uncertain
effect semantics. No live config or state is changed. Sources:
`jvagent/cli/harness_migrate.py:592`,
`jvagent/cli/harness_migration_channels.py:110`.

The [reference assessment](2026-11-07-native-sdk-tool-policy-reference-blockers.json)
now has two blocker categories: reviewed script image/resource policy and
unqualified model-service callers/provider declarations. Its ten selected skills
do not constitute compatibility qualification; no reference candidate is emitted.
No remaining channel setting is a blocker in that particular assessment.

## Real provider browser evidence

[Persisted evidence](2026-11-07-native-sdk-tool-policy-smoke.json) and
[visible client snapshots](2026-11-07-native-sdk-tool-policy-browser.json) record
an isolated disposable PostgreSQL app on backend8183/UI3144 and actual GPT-4.1.
The UI is a frozen byte-for-byte copy of the current built client, with separate
artifact hashes. The observer records source blobs and module loading without
patching runtime functions; no legacy execution modules load.

The public CLI-generated fixture preserves modular declarations and selects a
one-second default-channel tool limit under a two-second base. The probe has no
Action-local timeout, so these tests exercise the migrated host policy.

| Turn | Expected and observed | Model requests |
|---|---|---:|
| MCP slow read, fallback | Failed RPC, verified Cedar/14:30; both receipts terminal | 4 |
| Action slow read, fallback | Failed read, verified SDK-DEADLINE-VERIFIED-58 | 3 |
| Explicit marker write | One accepted marker, unconfirmed tool, unknown outcome | 2 |
| Follow-up retry | Recovery refusal, no model/tool body | 0 |
| Cold continuation | Actual fast read returns the same verified value | 2 |
| New independent marker intent | Second accepted marker, unknown outcome | 2 |
| Follow-up retry | Recovery refusal, no model/tool body | 0 |

First four turns use worker9705 before the final pre-intent receipt repair;
final three use worker10559 and the final recorded source. They are not relabeled
as a single source epoch. Both uncertain writes remain started/annotated, while
all read/loader effects are terminal. Two marker lines represent two explicit
independent intents, never an automatic retry. Cached reload restores the
unconfirmed warning. Cold/reload comparisons preserve interactions,
conversations and run registrations exactly.

All seven turns close. Thirteen requests / 8,445 provider-reported tokens /
USD 0.018372 estimated cost reconcile exactly to conversation journals; no
unknown cost calls. Dollar amounts are library-pricing estimates, not reported
provider invoice charges. Each turn records elapsed time, SDK events, outcome,
selected limits and usage provenance. Failed run `usage_complete=false` remains
conservative even when all observed provider receipts reconcile.

## Checks and limits

Final source native/host/PageIndex/web-fetch/real PostgreSQL/reviewed Docker lanes
pass **939 per Python 3.11 and 3.12**, no skips in those lanes. Migration/scaffold
lanes pass **226 per version**. Transitional suite passes **5,139**, with **73
skipped**; focused changed tests pass **162**. Detailed commands, source restoration
and subsequent commit-gate results belong in the linked test evidence.

Actual-provider delayed pre-intent admission, delegated cancellation and Docker
deadline are not live-browser-qualified here; deterministic SDK tests and actual
Docker/stdio regressions cover those branches. MCP connection setup, hard Python
preemption, external-write reconciliation, real Meta/voice transports, retained
legacy state, whole-app cutover, deletion, deployment/concurrency and comparative
performance remain separate gates. The earlier precise denied-tool UI gap is
still open. No optimization or enterprise qualification is claimed.
