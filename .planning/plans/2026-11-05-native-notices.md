# Continuation through blocked host notices — 2026-11-05

Parent `67e434d001be1473621da7c7f71e92ac58d9b475`; all nine parent CI checks passed.
The earlier reserve operation could resolve an interruption only while that run
was the immediate predecessor. Once a user received a blocked reply, that reply
had no SDK registration and itself prevented subsequent admission. A regression
reproduces the failure on the parent before implementation.

## Thin history projection

`history_for_new_turn` resolves at most 32 [blocked recovery notices](../GLOSSARY.md#blocked-recovery-notice),
then invokes the existing SDK-backed history policy for the original run. Only after
that succeeds are intervening user prompts and literal host notices supplied as
public typed ModelRequest/ModelResponse history. The SDK still owns generation,
tool execution, compaction, persistence and recovery frontiers. No request is replayed,
no checkpoint/edge is rewritten, no intervening user message is silently discarded and no additional
store or execution service was added (`jvagent/harness/recovery.py:26`, `:52`, `:103`).

The notice must match the existing host response exactly, be closed and emitted,
belong to the same caller/conversation/session, have only NativeHarnessAction
execution, and contain no directives, metrics or SDK records. Its declared predecessor
must match the real graph neighbor, with valid chronology. Cycles, broken links,
other rejection types, extra run fields, unqualified Actions and excessive depth
fail closed. Empty/unqualified history retains NativeHistoryError classification.
The host response is shared as a constant without changing its content
(`jvagent/harness/recovery.py:18`, `:52`,
`jvagent/action/native_harness/native_harness_action.py:304`).

The original SDK run must still pass all existing identity, terminal-tree, checkpoint,
effect and usage checks. A notice never authorizes provider billing or bypasses an
unresolved effect. History-source metadata records the original run and chronological
notice IDs; intervening text stays user-role content, not instructions with host
authority (`jvagent/harness/recovery.py:103`).
The existing accepted-input policy still excludes undelivered model outputs.

Admin inspection reports recovery_interaction_id and blocked_interaction_ids, allowing
an operator to inspect/approve the original run. Applying a reserve directly to a
notice is refused; estimates remain confined to terminal failed/cancelled executions
(`jvagent/action/native_harness/endpoints.py:83`,
`jvagent/harness/reconciliation.py:86`). This is JV ingress transcript adaptation,
not an upstream SDK patch or a second replay algorithm.

## Actual-provider browser sequence

Disposable graph/app 8136, jvchat 3136, real OpenAI GPT-4.1. Runtime functions were not
patched. Source blobs, process IDs, typed snapshots, host flags, events, request
outcomes, durations and cost provenance are in the linked records.
Snapshot content hashes carry a `sha256:` display prefix to distinguish checksums
from credentials; graph values and message content are unchanged. These evidence
exports are not checkpoint import payloads.

1. Cancel after a persisted SDK model-request start:
   [original state](2026-11-05-native-notices-cancelled-smoke.json). Usage remains
   unknown; no tool effects occur.
2. Send two requests containing distinct codes. Both receive the recovery notice:
   [first](2026-11-05-native-notices-first-block-smoke.json) and
   [second](2026-11-05-native-notices-second-block-smoke.json). They have zero model
   attempts, empty SDK stores and no spend increments.
3. Inspect the latest notice: the endpoint identifies the original cancelled run
   through both links. Notice-targeted reserve returns 422; original-run approval
   succeeds: [acknowledged graph state](2026-11-05-native-notices-approved-smoke.json).
4. Restart the backend, then request both codes without repeating them. The model
   replies `JUNIPER-28 MARIGOLD-91`:
   [fresh final graph/checkpoints](2026-11-05-native-notices-final-smoke.json).
   The new input checkpoint contains both intervening user messages. Original events,
   effects and unknown status remain intact; no legacy execution module loads.

[Browser responses and operator probes](2026-11-05-native-notices-browser.json)
record the visible notices and reply. The completed follow-up uses one request,
1,334 reported tokens, USD 0.002746 estimated, 2.339 seconds SDK boundary duration
and 3.952 seconds Interaction wall time. Blocking requests take 0.239/0.237 seconds
without a model call. The separate interrupted attempt remains unknown and unmeasured;
its USD 0.01 policy reserve is explicitly estimated. Conversation counters total
USD 0.012746. Provider-invoiced dollars are unavailable.

The first follow-up was submitted before restart completed and hit a network error.
[Readback at that point](2026-11-05-native-notices-restart-network-error-smoke.json)
proves no new Interaction or budget change. One explicit manual retry succeeded.
Its transport-error duration was not instrumented and is recorded as unknown.
The browser retains both local submissions; SQL contains only the accepted follow-up.
[Reload/selection](2026-11-05-native-notices-restoration.json) restores the reply and
history. Per-message transport-failure presentation remains a client qualification gap.

## Validation and open gates

319 native/real-PG tests pass on each Python 3.11 and 3.12. PG cases cover immediate
and deferred saves with fresh root/notice readback. Adversarial tests cover caller,
conversation and session changes, forged links, extra run fields, hidden SDK/metric
activity, other Actions, open/unemitted or nonliteral notices, pruned roots, cycles
and depth limits. An operator lookup test checks original-run resolution and refusal
of notice-targeted reservations. Eighteen affected host/history endpoint tests pass.
All-file hooks pass after formatting is rechecked. Full pre-push tests and new-head
CI remain independent publication gates.

This qualifies the literal, nonexecuting notice class. Other host failures, changed
notice schemas, mixed-Action notices, pruned/ambiguous graph state and depth beyond
32 require operator repair. Effects, uncertain delivery, abandoned/distributed
ownership, invoice settlement/correction, broader skill/action/provider/channel
compatibility, migration and legacy deletion remain open. No release or general
performance qualification is claimed.
