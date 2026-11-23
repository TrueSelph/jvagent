# Embedded cancellation and shared transport teardown

Parent `3cecca98`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent CI checks passed. See the governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[SQL/process/source receipts](2026-11-05-embedded-cancellation-smoke.json) and
[sanitized browser/wire observations](2026-11-05-embedded-cancellation-browser.json).

## Repair and ownership

The parent embedded stream requested cancellation on disconnect but did not await
SDK cleanup before returning and removing its task registry entries. Closing the
host generator could also leave its walker running. The new composed-SDK
regression failed with a missing terminal event before the repair.

HTTP and embedded teardown now share one 30-line transport helper. It forwards a
cancellation request and shields the wait while the official SDK/graph adapters
commit their own terminal state. Embedded registry removal runs after that wait,
under the same cancellation shield. It does not manufacture SDK events, replay
execution or create another state store (`action/interact/task_cleanup.py:10`,
`embed/interact.py:683`, `action/interact/endpoints.py:1374`).

Explicit embedded cancellation and replacement-stream registration also avoid
interrupting cleanup already in progress. Two alias keys and repeated cancel
requests now produce one cancellation. A separate-process mutation that restores
direct Task.cancel calls makes the regression fail with four cancellations instead
of one; the final implementation passes (`embed/interact.py:35`, `:68`,
`tests/test_embed_stream.py:21`). Native behavior requires Python 3.11+; the
transitional Python 3.10 HTTP/embed surface still runs its existing checks.

## Real browser results

An authenticated, administrator-only disposable host endpoint calls the public
`jvagent.embed.interact_stream` API. Caller identity comes from the server-owned
jvspatial authentication service; the agent is fixed by the fixture. The endpoint
is outside the repository and is not a shipped product API. Its exact source is
retained in the machine receipt. No provider or backend execution function was
patched. A browser fetch adapter routes jvchat turns through that host endpoint.

- GPT-4.1 performs a real MCP rehearsal lookup. jvchat shows the tool row, correct
  Cedar/14:30 answer and 681 tokens. The graph records delivered output and a
  completed read effect. Duration: 6.109 s in the Interaction, 6.341 s in browser.
- The real Stop button is clicked two seconds after the next start event. SQL
  readback records cancelled, usage_complete false, no output or delivery,
  official run_failed and one unknown-cost call. Browser duration: 2.473 s.
- A follow-up returns the existing recovery notice with no model requests or
  effects: 0.240 s in the Interaction, 0.349 s in browser.
- A fresh page using the ordinary HTTP interact route afterward returns HTTP-READY
  with delivered native state, 290 tokens and known estimated cost. It uses the
  same process/agent and a new conversation; no silent reuse of the blocked run.
  Duration: 3.632 s in the Interaction. The process observer has no legacy loop,
  executor, pilot runtime or HarnessRuntime imports through finalization.

Known subtotal: three completed provider requests, 971 reported tokens,
USD 0.002242 estimated. One interrupted request has unknown final billing and is
not counted as free. Provider-invoiced cost is unavailable. Registry cleanup and
repeated explicit cancellation are deterministic regressions; this browser cohort
qualifies transport disconnect, not a separately exposed cancel endpoint.

## Validation and remaining gates

Native/real-PostgreSQL suites: 234 passed on each of Python 3.11 (35.85 s) and
3.12 (36.69 s), 155 warnings each. The PostgreSQL matrix covers HTTP/embedded,
task-group/request-disconnect cancellation and batching on/off, with fresh SQL
terminal assertions. Affected interaction/response/embed suites: 292 passed
(11.05 s). Python 3.10 HTTP/embed checks: eight passed, one native-only skip.
All-file hooks passed; new evidence files require their final staged rerun.
Normal full-suite pre-push and exact-head remote CI remain separate gates.

This closes the basic local embedded-disconnect gap, not operator reconciliation,
unknown historical receipt migration, child/tool/write/publication cancellation,
non-cooperative cleanup deadlines, distributed ownership, arbitrary host identity
or egress recovery. No background/remote delegation claim follows. Interview,
flow and proactive compatibility, dependency/state migration, legacy/pilot removal
and representative release evaluation remain unfinished. The existing recovery
notice is a refusal boundary; a supported administrator settlement workflow still
needs implementation. The active goal is not enterprise-qualified.
