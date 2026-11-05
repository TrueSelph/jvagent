# Native HTTP cancellation durability and honest partial usage

Parent `9a056a1f`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent CI checks passed. Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[graph/process receipts](2026-11-05-native-cancellation-smoke.json), and
[sanitized browser wire/UI/timing](2026-11-05-native-cancellation-browser.json).

## Confirmed failures and repairs

Stopping a real GPT-4.1 Responses API request through Messenger cancelled the
native run, but PostgreSQL retained only `model_request_started`, without the
library's terminal `run_failed`. In-memory library and HTTP assertions passed.
A fresh database read reproduced the actual failure with batching enabled.
The missing durable event survived a backend restart. This was not a reason to
write a second journal or invent a terminal SDK event.

HTTP generator teardown now forwards cancellation once and shields its wait
from the request's AnyIO cancellation scope, allowing the official SDK lifecycle
to commit its own error snapshot and terminal event. Python 3.10 transitional
HTTP support guards the newer Task.cancelling API; native execution remains
Python 3.11+. Source: `jvagent/action/interact/endpoints.py:1374`.
The same PostgreSQL regression fails before the shield and passes afterward,
including both request-disconnect and task-group cancellation with deferred saves
on/off. The final real browser Stop also retains `run_failed` in a fresh SQL read.

That durable snapshot exposed a second defect: the SDK emits a zero-token,
zero-dollar SpendSnapshot for an interrupted response without reported usage.
JV treated its price-table zero as known cost. The intermediate browser receipt
retains this false-zero finding. Native accounting now uses public usage and
response lifecycle fields to distinguish measured completion from partial or
unreported usage. An empty or unfinished receipt has cost amount null and an
explicit usage status; any reported partial tokens remain counted. Both live
spend and checkpoint fallback paths use this rule. Completed token-limit receipts
with a finish reason retain their known usage. Sources:
`jvagent/harness/accounting.py:13`, `:78`, `:173`.
Two zero/partial-token regressions fail before this fix; replay of a partial
checkpoint stays unknown without duplicating receipts.

The SDK still owns cancellation, error hooks, run/snapshot/event semantics,
providers and pricing. JV supplies transport cleanup and truthful telemetry.
No execution engine, recovery loop, alternate token estimator or state DB was added.

## Real browser and graph outcomes

Disposable API 8136, Messenger 3137, PostgreSQL database
`jvagent_native_host_context`, deferred saves enabled, isolated browser contexts.
A one-shot browser observer reads only the SSE start and clicks the real Stop
button two seconds afterward. Backend/provider functions were not patched.

- Parent cancellation: no final answer emitted; native status cancelled, usage
  incomplete, one unknown provider attempt, no durable terminal event. Follow-up
  and post-restart requests refuse another model call with a visible recovery
  notice. The first long-guide control completed with a refusal before Stop;
  it is retained as a paid control, not counted as a cancellation success.
- Transport-only intermediate: the SDK terminal event is durable, but a false
  zero spend snapshot is visible. This is retained as a defect, not a free request.
- Final cancellation: status cancelled, usage_complete false, durable run_failed,
  one unknown-cost call, no output or delivery. The SDK terminal timestamp is
  0.063 seconds after the recorded UI Stop. This is one observation, not a latency SLA.
- Final follow-up returns the explicit recovery notice and creates no model
  requests, step journal or spend receipts. The blocked Interaction remains
  distinct from the original unresolved-cost run.
- A separate fresh Messenger conversation after Stop returns FRESH-READY, with a
  delivered native result and normal usage. Final health also returns FINAL-READY.
  The ordinary MCP room/time read passed in the intermediate cohort. The process
  observer has no old execution modules through finalization.

Known completed calls for the entire round: five requests, 1,738 reported tokens,
USD 0.004112 estimated. Three additional interrupted requests have unknown final
billing; these are not included as zero in that known subtotal. Provider invoiced
cost is unavailable. Wire timings, persisted timestamps and all controls remain
in the evidence. Runtime blobs are sampled at backend startup; tracked blobs
include subsequent formatting-only line wrapping and regression additions.

## Validation and limits

Native/real-PG suites: 229 passed on Python 3.11 (30.63 s) and 3.12 (30.46 s),
145/147 warnings respectively before final fixture-only Decimal/deadline cleanup.
The touched cancellation/accounting cases reran on 3.12: 15 passed (6.69 s).
Affected ingress/response/embed tests: 291 passed (10.02 s). Transitional Python
3.10 HTTP smoke: two passed. All-file hooks passed; final new evidence files must
also pass staging-aware hooks before commit. Normal full-suite push and new-head
remote CI are independent gates, tracked on draft PR #260.

Cancellation tests use public FunctionModel, Chat Completions and Responses
transports, official StepPersistence, actual MCP stdio lifecycle and both graph
write modes. Their outer timeout allows cold MCP setup; it is not a cancellation
SLA. Fresh SQL assertions are mandatory: cached record access alone missed the
confirmed failure.

This is local HTTP/browser cancellation qualification. Embedded host cancellation,
delegated/tool/write/publication cancellation, non-cooperative stores, distributed
ownership, actual-provider timeout policy, operator settlement and recovery remain
open. Shielded cleanup has no new universal deadline; database/provider outage
behavior must be qualified separately. Old native false-zero receipts are not
retroactively reclassified by this increment; W5 migration must reconcile or reject
them before cutover. Historical missing terminal events are not fabricated. Unknown
billing still requires explicit reconciliation or a new conversation; the current
notice mentions an administrator, but a supported operator reconciliation workflow
is not yet implemented. Numeric cost totals remain known subtotals with unknown
counts, and usage timing fields still need repair. None of this completes migration,
legacy/pilot removal, the full compatibility matrix or enterprise release gates.
