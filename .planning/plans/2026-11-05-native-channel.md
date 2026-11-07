# Native background channel delivery — 2026-11-05

Parent `5e85c5ef7eda0822151d940f4fd578f7cf50392a`; branch
`feat/pydantic-harness-rebuild`, author `eldonm`. This is a bounded local
qualification increment, not enterprise release approval.

## Implementation and ownership

jvchat now consumes the existing read-only reply subscription between user turns.
It suspends that reader during its own interaction, retires late frames after
caller/session changes, deduplicates bus IDs/sequences, and saves received rows
through the existing caller-scoped history writer. Immediate interaction completion
still resumes subscription after transport cleanup. No new endpoint, model loop,
task engine or database was introduced (`jvchat/src/config/api.ts:925`,
`jvchat/src/hooks/useStreaming.ts:268`, `jvchat/src/lib/channelMessages.ts:1`).

Native delivery includes bounded aggregate usage and the actual response model
when available. Aggregate receipts do not invent per-call metrics. Tool groups
stop at an explicit Interaction boundary, so a scheduled tool call belongs to its
own answer (`jvagent/action/native_harness/native_harness_action.py:22`,
`jvchat/src/lib/threadMessages.ts:144`,
`jvchat/src/components/assistant-ui/message-stats.tsx:61`).

Native subscribe/publish authorization reuses canonical ingress identity, current
conversation ownership and interaction access policy. Invalid bearer credentials
cannot downgrade into anonymous access. Streaming revalidates before each frame,
including keepalives. Its subscriber queue is bounded at 256; overflow is explicit
and disconnects the reader without blocking publishers. Wire timestamps use the
shared UTC clock (`jvagent/action/reply/endpoints.py:158`, `:332`,
`jvagent/action/response/streaming.py:89`, `:157`,
`jvagent/action/response/response_bus.py:291`). Legacy authentication remains
transitional; this record does not qualify its revocation semantics.

## Confirmed findings and regressions

The original actual-model test completed SDK execution but failed publication:
ReplyAction.publish did not accept the new metadata argument. The first failure
record is retained. The method now explicitly accepts optional server metadata;
recipient/session ownership remains visitor-bound. Regression tests invoke the
real ReplyAction and ResponseBus, replacing the permissive fake publisher in native
scheduled-task tests (`jvagent/action/reply/reply_action.py:346`, `:423`,
`tests/action/reply/test_publish_metadata.py:1`,
`tests/harness/native/test_proactive.py:1`). An isolated parent-method probe fails
with TypeError. This was a real test-boundary gap, not a successful first smoke.

A second parent regression accepted a revoked, previously nonempty conversation
capability. The parent endpoint probe fails its expected-rejection assertion.
Current tests cover rejected admission, no invalid-bearer downgrade, secret/policy
revocation during streaming, queue overflow/unsubscription, and UTC timestamps
(`tests/action/reply/test_native_subscription.py:1`,
`tests/action/response/test_subscription_bounds.py:1`). Frontend regressions cover
caller/session retirement, hydration races, duplicate frames, immediate foreground
completion, parser/auth failures and aggregate observability.

## Actual browser and PostgreSQL evidence

Disposable app/graph: backend 8136, jvchat 3136, PostgreSQL
`jvagent_native_host_context`. Actual OpenAI GPT-4.1 calls; synthetic read-only MCP
fixture. Admin-only fixture routes queue/dispatch real TaskMonitor work. No
execution function was patched in the running process. Evidence records parent
revision, source blobs, observed runtime modules, persisted SDK state and usage.
Final runtime source hashes match the tested worktree, with no legacy execution
modules loaded.

- [First failed publication](2026-11-05-native-channel-first-failure-smoke.json)
  retains the paid execution and missing delivered result.
- [Initial live cohort](2026-11-05-native-channel-initial-live-smoke.json) and
  [browser/security observations](2026-11-05-native-channel-initial-browser.json)
  retain the intermediate UI grouping/aggregate-display defects. Subsequent code
  fixes are qualified by the separate final cohort.
- [Final provider/graph readback](2026-11-05-native-channel-qualified-final-smoke.json):
  ordinary READY reply delivered in 3.257s, 1,554 tokens, one request; scheduled
  REHEARSAL-47 lookup/answer delivered in 4.321s, 2,551 tokens, two requests.
  Combined 4,105 reported tokens, USD 0.006770 estimated; the provider did not
  report actual dollars. The domain task is completed, both Interactions are
  closed/emitted/delivered and the SDK read effect is recorded.
- [Final browser receipt](2026-11-05-native-channel-qualified-browser.json):
  READY and the scheduled Cedar/14:30 answer appear separately without another
  user message. The latter owns its MCP tool group, two-attempt badge, tokens,
  timing and explicitly estimated USD 0.0036 cost. Dispatch HTTP 200 took 4.603s.
- [After duplicate dispatch](2026-11-05-native-channel-qualified-after-duplicate-smoke.json):
  zero dispatched; persisted Interactions, task and budget counters exactly match
  the preceding readback. No additional provider requests were created.
- [Reload readback](2026-11-05-native-channel-reload-browser.json): page reload
  initially opens the new-chat view; selecting the saved conversation restores
  both replies, the tool group and usage without duplicates. This tests existing
  client history, not durable server backlog recovery.

The actual revocation probe connected with a session capability, rotated its
conversation secret, then published a synthetic marker through an authorized
owner bearer. The revoked reader received session_access_revoked and closed
without the marker; reconnecting with its old capability returned 401. The owner
UI correctly received the marker. That owner receipt is not a revoked-reader
leak. Secrets and dispatch lease identifiers are omitted from exported evidence.

## Validation and remaining gates

Python 3.11 and 3.12 native/host/real-PostgreSQL qualification: 333 passed each
(64.51s / 67.53s, 265 warnings each). Affected reply/response/native-host tests:
157 passed; real native publication tests: 19 passed. Frontend: 140 tests in 13
files; production build passed. Suites overlap and must not be summed. Staged
all-files hooks and the normal full pre-push suite are mandatory milestone gates;
their final results are recorded in the progress ledger/PR.

The bus backlog is bounded and process-local. Durable native cursor replay and
delivery reconciliation across backend restarts remain unsupported. One earlier
backend restart/reload returned the client to sign-in; final testing reauthenticated
and did not establish transparent credential recovery. Long outages, distributed
ownership, other channels/providers, external effects and performance under load
remain unqualified. The broader migration, dependency switch and legacy/pilot
engine deletion gates remain open. No optimization or enterprise claim follows
from this small browser cohort.
