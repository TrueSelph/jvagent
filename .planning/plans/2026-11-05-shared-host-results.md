# Native shared-host extraction

Parent `d4f67b99`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[extraction sequence](2026-11-05-shared-host-extraction.md),
[graph/process evidence](2026-11-05-shared-host-smoke.json), and
[sanitized browser SSE](2026-11-05-shared-host-browser.json), and
[visible UI](2026-11-05-shared-host-ui.json).

## Implemented boundary

- Enabled graph Actions select the execution owner; mixed native/other harness
  declarations fail. Graph lookup errors propagate instead of becoming an empty
  Action configuration (`core/execution_owner.py:7`, `action/actions.py:611`).
- Native admission creates a server correlation without legacy identity mirrors,
  leases or spans. Entry access checks and conversation mutation locking remain.
  The same correlation identifies SDK execution and tool/delivery receipts
  (`action/interact/interact_walker.py:362`, `:456`, `:622`,
  `action/native_harness/native_harness_action.py:310`).
- HMAC context verification and bounded, graph-backed nonce consumption moved
  unchanged to the shared host service. The old import is an alias to that same
  module, preserving extension imports and patched globals. Context is consumed
  after entry access, passed through SDK instructions, and a replayed empty native
  turn is refused (`core/host_context.py:194`,
  `action/orchestrator/host_context.py:10`,
  `action/native_harness/native_harness_action.py:261`).
- Agent-scoped buses refresh ownership from enabled graph configuration. Native
  publication keeps transport and stable IDs, without the legacy runtime outbox.
  Native durable cursor replay returns a visible unsupported error rather than
  reading legacy state (`core/agent.py:315`, `response/response_bus.py:415`,
  `response/streaming.py:75`). Native SDK snapshots/receipts remain graph-backed.
- Embedded task registration records native ownership before bootstrap, so host
  cancellation does not open the legacy recovery journal. Scoped queued replay
  preserves a native reply emitted before subscription; the callback queues
  synchronously (`embed/interact.py:27`, `:53`, `:460`, `:575`). Cancellation
  settlement still belongs to the native graph adapter, not a new host engine.
- Declared Redis/Dynamo ownership and multiple/invalid declared worker counts
  are refused at native startup/admission. Local conversation locking remains;
  no claim of detecting undeclared external worker pools or distributed fencing
  (`core/execution_owner.py:23`,
  `action/native_harness/native_harness_action.py:101`).

## Browser and persisted results

Four actual GPT-4.1 browser turns in the existing disposable InterviewAction/MCP
host app used seven requests and 2,402 provider-reported tokens. Estimated cost was
USD 0.005686; no unknown-cost requests were recorded. The successful lookup
returned Cedar / 14:30. A missing-record lookup explicitly reported failure and
saved a failed tool effect. A final successful lookup after runtime restart
confirmed the admission/publication changes. A follow-up after the final runtime
restart recalled Cedar / 14:30 from persisted native history with one request and
no further tool call.

Backend durations were 6.953052s, 4.407075s, 5.208917s and 4.925566s. Each
request returned HTTP 200 with start, literal reply and final events; the three
lookup requests also include tool call/result events. All four
Interactions are closed/emitted with delivered native receipts and matching
server correlation, SDK/effect and reply identity. The process module observer
found no legacy HarnessRuntime, executor or loop through publication/finalization.
Only the orchestrator package and shared constants were loaded. This is a
functional cohort, not a latency or cost comparison.

The browser automation's textbox fill did not always update frontend input state;
a normal keystroke did. The completed SSE already contained its terminal frame.
The served client still retains earlier messages visually after New Conversation,
as recorded in the earlier cohort; graph sessions are separate. Neither observation
is treated as evidence of model timeout or durable cross-session replay.

## Regression coverage and remaining gates

The process regression now exercises caller admission, signed nonce graph write,
publication, finalization and empty replay rejection before asserting absence of
legacy runtime/executor/turn-cache imports. Existing alias export tests still
request the old classes explicitly. New regressions cover graph failure, mixed
owners, declared topology refusal, bus ownership refresh, native cursor refusal,
embedded cancellation ownership and fast-response replay. Existing HMAC
caller/expiry/nonce tests and native cancellation/accounting tests are retained.

An initial full-suite run had two fixture failures: new real Agent fixtures lacked
required declaration fields. They were corrected and targeted tests passed. The
old registry test had used uninitialized Agent objects and could compare two None
values; it now asserts a real shared bus. Final full-suite, native Python 3.11/3.12,
all-files hooks and push results are recorded in the progress ledger.

Still open: actual-provider signed-host/embed/popup and ordinary-member browser
matrix; provider/tool/delivery cancellation faults; distributed ownership,
durable delivery reconciliation, interview execution, proactive/flow/write
compatibility, migration/deletion, complete workload evaluation and release gates.
Explicit transitional configurations still load legacy implementations. This
increment removes that dependency from tested native turns; it does not ship the
required final single-path release or establish enterprise qualification.
