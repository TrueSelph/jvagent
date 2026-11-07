# Offline channel policy migration

Parent `ef22dabe`. The public CLI now converts qualified channel declarations
into a new native candidate rather than blocking every nonempty map. Sources:
`jvagent/cli/harness_migration_channels.py:23`,
`jvagent/cli/harness_migrate.py:583`. See the
[migration contract](../runbooks/native-harness-migration.md),
[machine evidence](2026-11-07-native-channel-migration-smoke.json),
[browser evidence](2026-11-07-native-channel-migration-browser.json) and
[current reference blockers](2026-11-07-native-channel-migration-reference-blockers.json).

## Translation and refusal boundaries

Exact-channel literal extras and soft response-length guidance become SDK
`channel_instructions`. Positive runtime/cost ceilings and request ceilings become
tightening-only `channel_limits`; explicit zero activation preserves the old
clamp to one. Typed engine heuristics use the same retirement declaration kinds
as global controls. Exactly empty tool lists and typed inactive controls can
retire. The report names retired settings and requires review of the changed
single-model/context/event semantics. Domain SOPs belong in skills.
Sources: `jvagent/cli/harness_migration_channels.py:7`, `:66`, `:149`.

Nonempty pins/tool restrictions, nonzero tool deadlines, invalid/unknown controls,
zero runtime/cost or widening ceilings block the entire candidate. No channel
label grants authority. Reports expose known blocked setting names, not channel
labels, private instruction values or unknown key names. The original YAML and
graph remain untouched; only a new operator-controlled candidate may be written.
Sources: `jvagent/cli/harness_migration_channels.py:100`, `:128`,
`jvagent/cli/harness_migrate.py:584`.

The initial regression exposed instruction ordering that depended on YAML key
order. Composition now places literal extras before soft length guidance
explicitly. Tests cover valid translation, exact types, redaction, source
immutability, unsupported controls, invalid unmatched profiles, widening and
simultaneously invalid global/channel settings.
Source: `tests/cli/test_harness_migrate.py:733`.

## Public CLI and actual-model proof

The CLI wrote a new 0600 candidate from a preserved legacy-source fixture. Its
reported source digest equals the preserved source after generation. A reviewed
candidate was then applied only to an empty disposable PostgreSQL app/backend8181.
The final planner reproduces the identical browser-tested candidate after a
report-only diagnostic improvement. Observed runtime source blobs and final
planner blobs are separately recorded; old observations are not relabeled.

Three GPT-4.1/jvchat turns return 133, 140 and 147 with the migrated presentation.
Every delivery persists request/time/cost ceilings of 2/30 seconds/USD 0.25, below
base 8/60 seconds/USD 2. The primary skill is activated by the official SDK; no
deferred load-capability tool event is expected. Selected factory instructions
contain the exact default profile and exclude the unmatched profile. No external
effects or model/tool runtime functions are patched.

Three requests/985 provider-reported tokens/USD 0.002330 estimated reconcile with
the conversation journal; unknown-cost calls are zero. Cold restart preserves
the original two interactions and conversation exactly. Final cached reload
restores replies/statistics without changing any of the three interactions,
conversation or run registrations. Durations/outcomes and native receipts are
in the machine record. This qualifies generated configuration on an empty app,
not retained-state migration or real voice/Meta delivery.

## Remaining gates

The migration slice passes 196 tests on each Python 3.11/3.12. The transitional
suite passes 5,139 cases, with 73 skips and 31 warnings. Its optional SDK skips
are not native coverage; the installed migration lanes independently exercise
the converter. Removing the
widening guard makes three adversarial cases fail. Source bytes are restored
before final affected tests and all-file gates. Runtime/SDK source is unchanged
from the preceding qualified milestone; this round changes offline conversion.

The unchanged reference still has three blocker categories: reviewed script
image/resource policy, unqualified model-service callers and channel policy.
The channel report now identifies `pinned_tools` and `tool_call_timeout` as the
remaining unsupported settings. Ten selected skill formats pass admission; no
reference candidate is generated. Retained tasks/state, whole-app cutover,
legacy/pilot deletion, wider recovery/providers/deployment and comparative
performance remain open. No enterprise or optimization claim is made.
