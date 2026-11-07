# Exact-channel instructions through the SDK

Parent `a5746ec7`. This supplies the presentation prerequisite for migrating
legacy channel overrides, without recreating their generic execution-knob map.
See the [runbook](../runbooks/native-channel-instructions.md),
[machine evidence](2026-11-07-native-channel-instructions-smoke.json) and
[browser observations](2026-11-07-native-channel-instructions-browser.json).

`NativeHarnessAction.channel_instructions` admits literal text per exact channel.
All profiles validate at startup and per turn. Only matching text appends to the
base instructions passed to the public SDK factory; unmatched channels receive
the base alone. `default` has no wildcard semantics. There is no prompt renderer,
tool selection, provider policy or execution loop in this adapter. Sources:
`jvagent/harness/instructions.py:6`,
`jvagent/action/native_harness/native_harness_action.py:508`.

The host channel remains a routing fact, not authorization. Tool grants, active
skill ownership and resource limits are independently enforced. Domain procedures
remain in authored skills; presentation guidance is not deterministic validation.
The offline CLI still refuses nonempty legacy channel overrides, whose remaining
deadlines/denials and service behavior need explicit migration. Sources:
`jvagent/harness/instructions.py:6`, `jvagent/cli/harness_migrate.py:180`.

## Actual model/browser evidence

Disposable PostgreSQL/backend8180 and jvchat3140 use GPT-4.1. A `default`
instruction supplies an observable prefix; a `default_extra` instruction supplies
a distinct marker that must not be selected. Three turns answer 133, 140 and 147
correctly with the selected prefix. The middle user message asks the model to
pretend it is on `default_extra`; it still uses the configured default profile.
After a terminal restart, a same-session follow-up works from persisted history.
An observation-only factory trace confirms selected instructions are present and
unmatched text absent. Runtime functions are not patched.

Four requests, 1,281 provider-reported tokens and USD 0.002946 estimated cost
reconcile with the conversation spending journal, with zero unknown-cost calls.
Cold restart preserves both original interactions and the conversation exactly.
Selecting the cached conversation after final reload restores the response,
skill-load event and usage; all three interactions, conversation and registrations
remain exactly unchanged. The browser briefly navigated before cold startup was
ready and returned to login; normal login after readiness restored access. No
model call occurred during that setup interval.

Only the default browser transport is exercised. Exact nonmatching/prefix labels
and malformed unmatched declarations have deterministic coverage. Actual
voice/Meta delivery, multilingual channel conventions, operator config migration,
retained-state cutover, legacy deletion and comparative performance remain open.
All three reference configuration blockers remain. This is not enterprise or
whole-app migration qualification.

## Verification

The affected ingress/instruction slice passes 78 cases on each Python 3.11/3.12.
The transitional suite passes 5,138 cases, with 73 skips and 31 warnings. Initial
native qualification omitted the script root (six skips); the next attempt used
an unbounded host directory (six explicit refusals). A focused retry also used
an abbreviated image ID, which validation rejected. With the existing bounded
test volume and full reviewed image ID, all 21 script tests pass on 3.11; the
remaining 892 native/host/real-PostgreSQL cases had already passed. These setup
failures are retained rather than presented as successful qualification.
The complete Python 3.12 native/host/real-PG lane passes 898 cases with 448
warnings and no skips. Removing exact-channel selection makes two regressions
fail; source bytes are restored before rerunning the affected slice and gates.
