# Native Action deadlines and uncertain writes

Parent: `357e4047`. The [smoke record](2026-11-06-native-tool-deadlines-smoke.json)
identifies tested source blobs, fixture hashes, frozen client assets, per-turn
events, persisted effects, duration, accounting and cohort boundaries. The
[browser record](2026-11-06-native-tool-deadlines-browser.json) preserves visible
final-source responses. See the [authoring runbook](../runbooks/native-tool-deadlines.md).

## Defect and engineering boundary

Pinned Pydantic AI converts FunctionToolset timeouts into retry control flow.
Against the actual native factory, both a subsequent model retry and a queued
mutation could execute a second write after the first write had already happened.
The original two reproductions failed with two write bodies each. The existing
receipt wrapper now stops annotated mutations on SDK retry/failure controls with
`MutationOutcomeUnknown`; it does not infer rollback, manufacture acknowledgement
or replace SDK scheduling. Source: `jvagent/harness/read_retries.py:38`.

Action-owned `Tool.timeout_seconds` and `@tool(timeout_seconds=...)` map to the
public SDK `Tool.timeout`. Metadata is validated at construction and again at
native admission. Direct Python methods retain direct-call behavior. Sources:
`jvagent/tooling/tool.py:9`, `jvagent/tooling/tool_decorator.py:84`,
`jvagent/harness/native.py:113`, `jvagent/harness/native.py:216`.

The first browser cohort exposed a second defect: the client showed a success
check for an unresolved write. Final source projects typed SDK failed/retried
results as errors and displays missing acknowledgements as unconfirmed warnings.
The original records remain unchanged. Sources:
`jvagent/action/native_harness/native_harness_action.py:74`,
`jvchat/src/lib/toolStatus.ts:4`.

## Actual provider/browser qualification

Disposable backend8179/PostgreSQL and final frozen jvchat3142 used GPT-4.1.
The owned test Action supplies explicit asynchronous timeout probes and a local
fsynced marker write; runtime functions were not patched. The first read/write
cohort precedes the display fix. Final source repeats both workflows and tests
follow-ups in both uncertain conversations.

- Both slow reads fail at the declared SDK deadline; fast reads deliver the
  actual file value `DEADLINE-READY-37`. Final UI labels slow read failed.
- Each separately requested write appends exactly one local marker, then times
  out before acknowledgement. Both runs stop with an annotated unresolved effect;
  neither continues to another model call. Final UI labels the write unconfirmed
  with a warning icon and explains the missing acknowledgement.
- Both same-conversation retries are blocked before any model attempt. These are
  intentional negative tests, not successful model answers.
- Six interactions/four conversations reconcile to 12 model requests, 5,312
  provider-reported tokens, USD 0.011836 estimated and zero unknown-cost calls.
  Unknown effect outcome is distinct from unknown model usage.
- Cold restart preserves the first two rows/conversations exactly. Final reload
  preserves all six rows, four conversations and run registrations exactly,
  with two markers and no extra execution/spend. Selecting the cached conversation
  restores the unconfirmed warning and model statistics.

Fresh-profile bookmarks alone did not backfill the archived transcript. Only
same-profile cache restoration is qualified here. Initial fixture startup refused
an invalid AccessControl reference; a later frozen-client CORS preflight failed
until its exact private origin was added. Both setup failures preceded the
corresponding model calls. Full setup notes remain in the smoke record.

## Verification and remaining gates

Final complete native/host/real-PostgreSQL lanes: 882 passed on Python 3.11 and
882 on 3.12, 448 warnings per lane, no skips. Full transitional suite: 5,121
passed, 73 skipped, 31 warnings. Frontend: 184 tests, lint and production build
pass. Skipped transitional cases do not substitute for the separate installed
native lanes. Regression coverage includes 16 mutation/fault/budget/batch cases,
invalid metadata, decorated read retry, typed result outcomes and client status.
Sources: `tests/harness/native/test_tool_deadlines.py:32`,
`tests/harness/native/test_native_events.py:45`,
`jvchat/src/lib/toolStatus.test.ts:1`.

Mutation checks remove the annotated-effect guard, retry-part error projection,
and missing-acknowledgement display branch separately. Each produces failing
regressions; original source bytes are restored before final gates.

SDK clocks do not hard-preempt blocking/cancellation-resistant implementations.
Legacy global/channel deadlines, MCP/delegation/script deadline equivalence,
external-write reconciliation and distributed recovery remain open. All three
reference configuration blockers, retained-state cutover, legacy deletion,
comparative evaluation and enterprise/release qualification remain open.
