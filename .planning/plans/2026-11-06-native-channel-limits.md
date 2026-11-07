# Native channel resource ceilings

## Architecture and limits

The remaining legacy `channel_overrides` block mixes resource ceilings with
prompt text, tool-surfacing heuristics and tool deadlines. This increment supplies
the resource-policy prerequisite; it does not copy that override engine.
`NativeHarnessAction.channel_limits` selects exact-channel **tightenings** of
existing run ceilings (`jvagent/harness/run_limits.py:47`). It admits no model,
prompt, tool, permission, skill or conversation-budget overrides.

All declared profiles validate at startup and again before factory construction,
including unmatched channels. Invalid types, unknown fields, nonfinite/disabled
limits and widening are refused without echoing values. Base settings and the
configuration map are not mutated. The chosen scalar values reach both the
official Harness spend policy and public SDK `UsageLimits`/run timeout. They
persist in the host run receipt through success, failure and cancellation
(`jvagent/action/native_harness/native_harness_action.py:496`). The catalog also
uses the existing captured channel rather than rereading a mutable visitor.

Channel labels grant no authority. An unmatched label uses the base ceilings;
`default` is an exact key, not a wildcard. Conversation spend remains shared.
SDK checks follow response accounting and do not establish provider-side or
distributed reservations. Procedures remain skill-authored. The six admitted
fields and configuration example live in the [run limits guide](../runbooks/native-run-limits.md).

## Actual-provider browser qualification

[Machine evidence](2026-11-06-native-channel-limits-smoke.json) binds parent
`8048fcf5`, source blobs, worker epochs, actual projected skill hashes, graph
readbacks, durations and usage. [Browser snapshots](2026-11-06-native-channel-limits-browser.json)
retain replies, tool activity, the explicit failure notice and statistics. The
isolated PostgreSQL app runs backend 8178/source jvchat 3140 with actual OpenAI
GPT-4.1. Observational profiling patches no runtime functions and observes no
legacy executor modules. The personal deployment/graph is not used.

1. With base request ceiling six and exact `default` ceiling one, the model
   loads the general skill. The SDK refuses the next request. The UI shows a
   budget notice, one attempt and 231 reported tokens; the failed receipt retains
   the applied limits and measured cost. This is an intentional failure, not a
   successful model answer or unknown usage.
2. After confirmed terminal worker exit, a reviewed disposable-only source sync
   raises that profile to three, still below base six. The existing Interaction
   and Conversation remain exact. A fresh browser conversation loads the skill
   and answers 19 × 7 = 133 in two requests. Its receipt records ceiling three.
3. Another terminal cold restart, without configuration update, preserves both
   Interactions and Conversations exactly and restores the persisted profile.
   A follow-up in the successful conversation answers 17 × 8 = 136 in one request,
   without another skill load. Reload restores records without execution/spend.
4. Retrying in the original budget-refused conversation now succeeds in two
   requests. Accounting recovery refreshes only the old receipt's `usage.last_updated`;
   prior tokens, costs, SDK history, effects and failed status remain unchanged.
   This timestamp change is retained explicitly rather than called an exact
   old-row comparison. Final reload is exact for all four Interactions and both
   Conversations, including counter values. No domain tasks are created.

Three turns deliver model answers and one intentionally hits the SDK budget.
All four requests close; each has one terminal SDK run event and each tool effect
has one completed terminal event. Six model calls consume 1,736 provider-reported
tokens; USD 0.003994 is estimated, not provider-billed cost. Unknown-cost count
is zero. Shared conversation journals reconcile exactly, including the failed
turn, without counting its prior usage twice on retry. All three owned workers
exit 143 and the owned browser page closes.

## Regressions and gates

The 28-case resource slice checks exact matching, immutable configuration, strict
types, invalid unmatched profiles, unsupported prompt/model/conversation policy,
all widening dimensions, public SDK request enforcement, concurrent task
isolation, SDK cancellation on deadline and startup refusal
(`tests/harness/native/test_channel_limits.py:1`). Existing host receipt scenarios
now run with and without profiles; factory/run assertions verify consistent
cost/request/time policy through failure, cancellation and delivery boundaries
(`tests/harness/native/test_native_action.py:42`).

Removing profile selection makes two focused cases fail. Removing the widening
guard makes five cases fail. Original source bytes are restored. Affected slices
pass 126 on each Python 3.11/3.12. Complete native/host-context/PageIndex/web-fetch
lanes with real PostgreSQL and the reviewed Docker image pass **853 cases,
430 warnings, no skips on each version**. The full transitional suite passes
**5,114 cases, 72 skipped, 31 warnings**; native SDK tests run in the separate
qualified profile rather than being claimed through those skips.
All-file pre-commit checks pass, including formatting, typing, tracked-asset
coverage and secret scanning. Publication still uses the normal commit/push hooks.

## Remaining work

The reference app still has the same three configuration blockers: reviewed
script image/resource policy, legacy channel overrides and model-service callers.
No automatic channel migration, legacy policy retirement, whole-app graph cutover
or old-engine deletion is claimed. Channel-specific procedures must move to
skills explicitly. Per-tool timeouts require independent qualification of SDK
cancellation/retry and uncertain-effect semantics; a run deadline cannot prove
that an external operation did not happen. Actual jvvoice/Meta transport, broader
deployment/recovery, nested-delegation/profile interactions and representative
comparative evaluation remain open.
No performance or enterprise-release claim is made; the full goal stays active.
