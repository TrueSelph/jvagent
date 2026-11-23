# Native completed reply readback — 2026-11-05

Parent `84de5f488e72ceb50b181d4509c71dc04a935549`; all nine parent CI checks
passed. Branch `feat/pydantic-harness-rebuild`, author `eldonm`.

## Scope and architecture

The existing reply subscription can now recover a bounded window of completed
native replies after a process restart, including persisted tool traces and usage.
It reads existing graph state and public SDK run/event records. It does not call
models, run tools, publish again through the bus, invoke channel adapters, or add
a state database/journal (`jvagent/action/response/native_replay.py:9`).

NativeHarnessAction strictly persists a versioned delivery receipt before
publication and flushes the delivered result afterward. The receipt contains only
stable message identity, channel and aggregate numeric/model observability;
approved text remains in Interaction.response. Final write failure leaves the
in-memory outcome uncertain. Normal native publication explicitly overrides any
caller-supplied replay flag (`jvagent/action/native_harness/native_harness_action.py:22`,
`:437`, `:452`). ResponseBus now includes original message IDs in its existing
persisted trace entries (`jvagent/action/response/response_bus.py:616`, `:1256`).

The projection requires matching caller/conversation/session, delivered receipt,
closed/emitted nonempty response, correct root SDK registration and terminal
run_completed event. Pending, running, failed, cancelled, uncertain and historical
receipt-less rows are excluded. It projects governed response text, not the raw
model output. It returns only explicit public receipt fields, not arbitrary
request/host metadata (`jvagent/action/response/native_replay.py:24`).

The stream subscribes before reading graph history, then deduplicates graph,
backlog and queued overlap. Existing native authorization rechecks every frame.
jvchat accepts these explicitly marked readback frames for a foreground turn as
well as scheduled work, using existing IDs/history storage to avoid duplication
(`jvagent/action/response/streaming.py:52`, `:117`,
`jvagent/action/reply/endpoints.py:327`, `jvchat/src/hooks/useStreaming.ts:283`).

The window is the latest 20 Interactions, at most 200 trace entries each; omitted
trace count/window are explicit metadata. Native cursor pagination is still
unsupported. The existing Conversation reader hydrates only that window but
currently materializes/sorts all matching raw rows; long-session database scaling
remains an open gate (`jvagent/memory/conversation.py:689`). Old tool traces without
message IDs and old native runs without delivery receipts require migration.

## Real browser/provider/restart evidence

Disposable app/graph: backend 8136, jvchat 3136, PostgreSQL
`jvagent_native_host_context`. Actual GPT-4.1, read-only MCP fixture; no execution
functions patched. Capture scripts record parent revision, source blobs, observed
process/modules, visible messages/events, SDK records, task state and spending.

1. [Initial actual-model cohort](2026-11-05-native-replay-before-restart-smoke.json)
   and [browser receipt](2026-11-05-native-replay-initial-browser.json) delivered
   READY and the scheduled REHEARSAL-47/Cedar/14:30 answer.
2. The cache was reduced from five rows to its one user row, and backend PID
   95993 was deliberately terminated with SIGKILL after both turns completed.
   [Initial restoration](2026-11-05-native-replay-initial-restored-browser.json)
   proves a fresh empty bus restored both replies, tools and usage.
   [SQL readback](2026-11-05-native-replay-initial-restored-smoke.json) preserves
   the intermediate source variant. A timestamp review caught app-local naive
   Interaction timestamps in this projection. The final implementation instead
   uses the official SDK terminal event's absolute timestamp.
3. [Final actual-model cohort](2026-11-05-native-replay-final-before-restart-smoke.json)
   and [browser](2026-11-05-native-replay-final-browser.json): READY delivered in
   3.273s; scheduled MCP answer delivered in 3.560s (dispatch HTTP 200, 3.875s).
   Three provider requests, 4,108 reported tokens, USD 0.006776 estimated.
   Actual provider dollars were not supplied. Initial and final cohorts together
   used six provider requests, 8,210 reported tokens, USD 0.013540 estimated.
4. [Cache removal](2026-11-05-native-replay-cache-removal.json) again leaves one
   user row and zero assistant rows. Backend PID 96945 was deliberately killed
   after completion. The page was parked during the outage and reopened after
   startup. Fresh PID 97181 reported zero bus messages and zero cached assistant
   rows before selection. [Final browser restoration](2026-11-05-native-replay-final-restored-browser.json)
   shows both answers, the MCP group, tokens, attempts, timing and estimated cost.
   Zero interaction POSTs occurred. This does not rely on the old bus or cached
   assistant messages.
5. [Final fresh SQL](2026-11-05-native-replay-final-restored-smoke.json),
   [repeated selection SQL](2026-11-05-native-replay-final-reconnected-smoke.json)
   and [final repeated-read SQL](2026-11-05-native-replay-final-qualified-smoke.json)
   exactly match the pre-restart Interactions, task and budget counters. The bus
   remains empty. [Reconnect UI/cache](2026-11-05-native-replay-reconnect-browser.json)
   has five rows with five unique IDs, including four assistant/tool rows.
6. [Timed read-only request](2026-11-05-native-replay-timed-read.json): HTTP 200,
   four expected frames, first frame 102.3ms and all four 177ms. This is one local
   observation, not a latency target/benchmark. Subscription lifetime in the other
   captures measures an intentionally parked connection, not restoration latency.

Final runtime source blobs match the worktree; no legacy execution modules were
loaded. The provider cohort preceding the final restart used the same execution
code; the final projection only moved its SDK import behind eligible-row checks
so empty history does not unnecessarily import native dependencies.

## Regressions, checks and remaining gates

Nineteen projection tests cover stable IDs/text/trace/usage, repeatability,
serialization/UTC, subscribe-before-read and graph/backlog overlap, plus foreign
or incomplete histories. JSON and PostgreSQL scheduled-task tests verify readback
with deferred saves on/off without another model run. Restoring the parent
NativeHarnessAction._execute in an isolated test process makes the new real-egress
readback assertion fail (zero frames instead of one). Running server execution was
not patched. Frontend regression permits foreground readback and deduplicates it.
Projection tests are in `tests/harness/native/test_reply_replay.py:1`;
real scheduled publication/readback in `tests/harness/native/test_proactive.py:1`.
Two additional write-fault cases prove no publication before intent acknowledgement
and an uncertain in-memory outcome after final acknowledgement failure
(`tests/harness/native/test_native_action.py:69`). Final affected native-action/
projection reruns pass 36 cases on each Python 3.11/3.12, including those new cases.

Final combined native/host/real-PG suites: 356 passed on Python 3.11 (65.67s) and
3.12 (69.14s), 268 warnings each. Affected legacy reply/response/latch slice:
140 passed. Frontend: 140 tests, production build passed. The first expanded PG
run exposed six fixture-wrapper calls missing the newly parameterized deferred
argument; those were corrected and the full suites rerun. The legacy slice then
exposed an eager native dependency import on empty history; that was corrected
and rerun. These were not successful initial qualification runs. All-files hooks,
normal full pre-push and exact-head CI remain separate milestone gates.

This increment does not reconcile pending/unknown publication, acknowledge client
receipt, guarantee exactly-once external delivery, or recover abandoned execution.
It does not qualify historical state migration, durable arbitrary cursor replay,
distributed workers, other channels/providers or long-session/load behavior.
The readback has no additional execution engine to remove: it remains a thin
channel projection over domain state and official SDK records.

Observed deployment limits: graceful shutdown waited for long-lived SSE readers
and required a second signal on the disposable backend. A reload during startup
returned the client to sign-in; a controlled restart with the page parked retained
credentials. Transparent recovery during an outage and bounded graceful shutdown
remain open, with the earlier scheduler event-loop-unavailable errors also requiring
deployment investigation. No process restart was triggered merely by an observation
timeout. Warnings include a supported but deprecated SpendLimits callback; migrate
to the public spend-event API before release. The complete rebuild goal remains active.
