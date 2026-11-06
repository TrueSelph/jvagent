# Native clock service and Action schema validation

## Implementation and ownership

The existing AgentUtils Action now publishes the existing `get_current_datetime`
name as a read service, with no model-selected timezone or host override. Its
fresh app-clock service has no harness dependency. The old core-tool wrapper
shares that service while awaiting deletion. Missing/invalid configured clocks
refuse rather than fabricate a date; configured zones require aware time and
unset zones normalize server-local time with an explicit offset. Sources:
`jvagent/action/agent_utils/agent_utils.py:19`, `jvagent/core/clock.py:8`,
`jvagent/action/orchestrator/core_tools.py:333`.

Offline conversion retires `tool_tier`: standard/full, including the old default,
retain or explicitly add AgentUtils; minimal adds none. Ambiguous/disabled service
or minimal with active service declarations require review. Candidate review
includes app timezone, skill ownership and host access; no source is overwritten.
Source: `jvagent/cli/harness_migrate.py:370`. See the
[migration runbook](../runbooks/native-harness-migration.md).

The malicious extra-argument clock test exposed a broader native gap. Public
Pydantic `Tool.from_schema` skips automatic argument validation. The adapter now
supplies its public `args_validator` hook using jsonschema and referencing, with
no coercion, remote schema retrieval or argument values in validation diagnostics.
It snapshots nested declarations; local references work. Native-only installation
now declares both standard libraries directly. Sources:
`jvagent/harness/native.py:169`, `jvagent/harness/tool_schema.py:14`,
`tests/harness/native/test_action_schema.py:1`.

Signature-derived schemas now distinguish nullable input from omission, support
PEP 604 optional types, and reject additional keys unless the callable explicitly
accepts keyword extras. Hand-authored schemas retain their declared policy.
Unknown annotations/multi-type unions remain incompletely described; this round
does not qualify every Python annotation. Source:
`jvagent/tooling/signature_schema.py:143`. The SDK retains tool scheduling,
retry, dispatch, lifecycle and persistence; no second execution engine was added.
Remove the bridge when the SDK provides equivalent schema-defined validation,
then remove unused dependencies.

## Real provider and persisted evidence

Parent revision ecd4be95; [machine evidence](2026-11-06-native-clock-smoke.json)
binds tested source blobs, process epochs, candidate report, persisted SDK state,
provider receipts, spend, request outcomes and durations.
[Browser observations](2026-11-06-native-clock-browser.json) retain visible tool,
reply and usage events. The disposable app uses backend 8161, jvchat 3140 and a
fresh PostgreSQL graph with America/Guyana time and actual GPT-4.1.

1. Actual offline conversion adds the service to a legacy declaration. The first
   request returns the exact tool timestamp and timezone with a visible tool call.
2. An authenticated admin endpoint denies the caller that named tool. The model
   attempts it and receives access denied; the visible reply honestly refuses a
   fresh timestamp without reusing history. Deterministic tests separately prove
   denied and extra-argument calls never read the app clock.
3. Restore permission and restart the backend. Readback proves the two original
   Interaction records, registrations and spend unchanged. The next browser turn
   returns a strictly later exact timestamp through a different worker process.
4. Reload restores all three replies, tool events and usage without another
   registration, changed Interaction, model request or spend debit.

The SDK records completed clock tool effects and a failed denied effect; these
are lifecycle facts for the read tool, not external writes. No domain task is
created. Tokens are provider-reported and costs explicitly estimated; all six
provider receipts reconcile against conversation accounting. Exact totals and
per-turn durations are in the machine record.

Initial fixture bootstrap refused an incorrect AccessControl package name before
any model work. The canonical declaration was corrected. The first broad test
run also found an obsolete test assumption: a nested object is now refused at
schema admission, producing the SDK retry result rather than interview JSON.
The updated regression explicitly checks that refusal and all remaining domain
rejections, plus unchanged repeated-item state; it does not suppress the failure.
Source: `tests/harness/native/test_interview_repeats.py:264`.

## Verification and limits

Native/host/real-PostgreSQL lanes: 587 passed on Python 3.11 and 3.12. Affected
clock/migration/schema/tooling slices: 239 passed on each version. Legacy core,
clock, tooling and schema audit: 128 passed, one native-only skip. Clean staged
source native-only installs pass dependency checks on both versions and directly
exercise successful and rejected schema arguments. Required staged all-files
checks and normal pre-push remain publication gates.

The read-only [reference report](2026-11-06-native-clock-reference-blockers.json)
has 12 blockers, down from 13 with the same full configured skill selection.
The clock path is locally qualified; malformed inputs, invalid timezone and DST
are deterministic tests rather than provider-induced failures. Broader Action
schemas/providers, custom extensions, retained-state migration, reasoning policy,
channel/vision/flow compatibility, distributed recovery, security/deployment,
evaluation and final legacy deletion remain open. No overall performance or
enterprise qualification is claimed.

The first full pre-push run refused publication: 5,037 passed, 60 skipped and two
legacy clock fixtures failed because they supplied an opaque object or missing
App while expecting the removed fallback. Their fixtures now bind a deterministic
UTC App clock and assert its exact timestamp through both the wrapper and native
legacy transcript. A separate missing-App test asserts explicit refusal. The
46-case affected catalog/protocol slice passes; runtime code and the tested
browser cohort are unchanged. The full pre-push must pass before publication.
Sources: `tests/action/orchestrator/test_catalog_and_tools.py:144`,
`tests/action/orchestrator/test_native_tool_protocol.py:560`.
