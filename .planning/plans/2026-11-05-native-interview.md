# Native interview domain tools and mutation receipts

Parent `226c705f`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent remote checks passed. Governing [execution plan](2026-11-04-harness-rebuild-execution.md)
and [interview adapter course](2026-11-05-interview-platform-state.md).

## Implemented

The official Skills workspace now receives the Action-owned native base SOP through
a generic Action asset hook. InterviewAction exposes explicit interview__start and
its reviewed built-in domain operations through a native-tool hook; legacy get_tools
remains unchanged for the existing release. The native path does not call use_skill,
a turn-lock router or an activation extractor. Native tool descriptions and the
versioned SOP describe the new API rather than the old activation grammar
(`jvagent/action/interview/native.py:18`, `:67`, `jvagent/action/interview/native_skill/SKILL.md:1`,
`jvagent/action/interview/interview_action.py:158`,
`jvagent/action/native_harness/native_harness_action.py:170`).

ConversationMutationTool is an explicit reviewed Action opt-in, not permission to
run arbitrary external writes. The factory requires visitor scope and SDK persistence,
uses public annotate_tool_effect before dispatch, and verifies the annotation exists.
The adapter checks the admitted Conversation's user/session/Interaction ownership
before and after execution and requires strict graph acknowledgement before returning.
The SDK alone schedules tools and records their lifecycle. Ordinary write-labelled
Tools remain rejected. Write denial and retry exceptions are terminal, and mutating
tools are excluded from delegated child tool lists. Legitimate Action arguments
named ctx remain supported through a positional-only SDK context parameter
(`jvagent/harness/conversation_tools.py:14`, `jvagent/harness/native.py:96`, `:118`, `:178`, `:364`).
See [official effect annotation semantics](https://pydantic.dev/docs/ai/harness/step-persistence/).

Failure after a marked mutation requires reconciliation, including a completed write
followed by model failure. Such a run cannot fall back to accepted input and erase
its write outcome from model context. Cancellation retains an unresolved started
effect. The public SDK record carries the marker; no extra journal, state database,
replay engine or inferred rollback was introduced (`jvagent/harness/recovery.py:70`).

The domain adapter reuses existing validation/session/review/reset/cancel handlers.
It rejects unqualified hook validators, processors, custom tools, complex field
extensions, legacy parameter directives and scheduled policies. Registered specs
retain their own inherited group/task policy, channel restrictions and disabled-tool
rules; activation does not bypass them. Per-tool callback admission refuses configured
environment/Action task webhooks and failed lookup, because those are external effects.
Terminal task state is checked so legacy close helpers cannot swallow a persistence
failure and produce a native success. Native tools retire duplicate task data.state
on the interview-managed tasks they touch; TaskStore.status is canonical
(`jvagent/action/interview/native.py:18`, `:107`, `:138`).

## Browser cohorts and graph readback

All tests use actual OpenAI GPT-4.1 through jvchat and the public authenticated HTTP
entrypoint, on the disposable 8136/3136 app and PostgreSQL graph. Execution functions
were not patched. Request capture observes cloned responses, removes session
capabilities and records no authentication headers. Source blobs and process IDs
identify each intermediate revision; do not treat earlier probes as final-source
coverage of safeguards added afterward.

| Cohort | Observed behavior | Fresh graph evidence |
|---|---|---|
| Initial three turns | Explicit start, invalid email rejection, corrected email, review, confirmation after backend restart | [Before restart](2026-11-05-native-interview-before-restart-smoke.json), [completion](2026-11-05-native-interview-final-smoke.json) |
| Task safeguards, two turns | Start/store/review, then confirm across another restart | [Review](2026-11-05-native-interview-qualified-before-restart-smoke.json), [completion](2026-11-05-native-interview-qualified-final-smoke.json); task completed, duplicate data.state absent |
| Final policy/signature safeguards, two turns | Fresh start without auto-store, then explicit cancellation across restart | [Active state](2026-11-05-native-interview-policy-before-cancel-smoke.json), [cancellation](2026-11-05-native-interview-policy-final-smoke.json); distinct session, task cancelled, live interview cleared |

The [initial/task browser receipt](2026-11-05-native-interview-browser.json) and
[final policy browser receipt](2026-11-05-native-interview-policy-browser.json) show
responses, tool events and usage. Seven delivered user turns used eighteen provider
requests, 25,827 provider-reported tokens, USD 0.029790 estimated and no unknown-cost
calls. Provider-invoiced dollars were not returned. Per-turn backend durations and
browser timings are in the receipts. These small cohorts do not establish a
performance improvement or general reliability rate.

Initial SQL exposed the stale data.state value; the second cohort verifies its
removal. Backend restart restored the same review session and task before completing
it. Reloading jvchat and selecting the saved conversation restored replies, tool
badges and token counts. A separate client defect remains: New Conversation created
a distinct graph session but retained earlier visible messages until further UI
state changes. Browser conversation-reset behavior is therefore not qualified.

## Validation

Final native, real-PostgreSQL and affected suite results are recorded in the
[validation receipt](2026-11-05-native-interview-validation.json). Real PG cases cover
immediate/deferred writes with fresh Task/Conversation/effect readback. The adapter
suite covers same-turn confirmation refusal, later confirmation, reset/cancel,
owner changes, save/flush failures, denied writes, unreviewed extensions/callbacks,
per-spec policy, failure after write, cancellation and unresolved effects. Existing
interview tests remain green. Separate mutation processes remove the recovery gate
or terminal-task check: the corresponding regressions fail (two and one expected
failures). The final implementations pass. All-file/commit hooks, full-suite push
and exact-head CI are independent gates; the live PR records publication status.

## Remaining course

This is a functional built-in interview vertical, not complete interview or enterprise
qualification. Continue with branch/for-each contracts, trusted custom validators and
handlers, custom tools and scripts, typed effect boundaries and external task callbacks.
Qualify multi-skill detours, parked/abandoned/scheduled task policies, delegated writes,
operator reconciliation and failure/outage/deployment recovery. Repair the client
conversation-reset defect and complete provider/channel coverage. Retire unused
legacy prompt-parameter injection and finish configuration/state migration before
removing legacy/pilot execution. Historical duplicate task fields are not globally
rewritten by this adapter. Upgrade/rollback, performance comparisons and single-path
release gates remain open under the governing execution plan.
