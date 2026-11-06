# Native skill catalogs across channels

## Defects and changes

A skill restricted to another channel previously blocked the entire native
catalog, including otherwise eligible ordinary skills. The existing declaration
adapter now projects exact allowed/denied channel lists before official Skills
composition. Denial wins; custom channel strings remain supported; family aliases
are not inferred. Duplicate names and malformed projected policy still refuse.
An unavailable primary or empty eligible catalog refuses before model work.
Action/tool prerequisites apply to eligible members. Sources:
`jvagent/harness/skill_assets.py:158`, `jvagent/harness/skill_catalog.py:44`.

The host uses the same pure projection before binding script assets. The host and
factory therefore share eligible source directories. Removing either projection
fails a regression. The asset-wiring case uses the real host path, bounded asset
reader and public SDK FunctionModel with a fake sandbox; it does not claim new
Docker execution coverage. Sources:
`jvagent/action/native_harness/native_harness_action.py:410`,
`tests/harness/native/test_channel_catalog.py:116`.

Official Skills owns loading/execution. There is no new router, classifier,
override engine, journal or persisted field. Omitted members do not contribute
tool owners, output contracts or fresh script staging. Channel is a routing fact,
not authentication or authorization: configured/loaded ownership and host
permission remain independent. Existing staged files in a granted user workspace
are not removed; channel metadata is not a filesystem isolation boundary.
Sources: `jvagent/harness/native.py:114`, `jvagent/harness/script_files.py:92`.

The actual clock retry exposed a second bug. App's valid default timezone=None
was rejected by read_app_clock. The service now treats only None and the existing
empty string as unset, preserving App.now() server-local behavior with an explicit
offset. Other falsy types, invalid zones/timestamps and naive timestamps under a
configured zone remain refused. The new None case fails against the former
implementation. Sources: `jvagent/core/app.py:69`, `:251`,
`jvagent/core/clock.py:8`, `tests/action/agent_utils/test_clock.py:20`.

## Actual-provider browser cohort

[Machine evidence](2026-11-06-native-channel-catalog-smoke.json) and
[browser observations](2026-11-06-native-channel-catalog-browser.json) bind source
epochs, visible replies/tools/statistics, persisted records, outcomes, durations
and usage. Fresh disposable PostgreSQL, backend 8169 and source jvchat 3140 use
actual OpenAI GPT-4.1. Runtime functions are not patched.

Ordinary web arithmetic succeeds while the clock skill is WhatsApp-only. An
explicit clock request receives a capability refusal without a tool attempt.
Forced SDK regressions establish omission enforcement independently of that model
refusal. An offline operator makes the skill web-eligible but denies its named
tool; cold restart preserves the records. The actual model loads it, calls the
clock and reports host denial. The SDK records the failed read and terminal event.

After permission restoration/restart, a first model reply repeats historical
denial without attempting the tool. Retain this as a failed recovery expectation.
An explicit retry reaches the service and exposes the None-timezone bug; that
failed run and its cost remain unchanged. The service fix receives another cold
restart, same-session retry, fresh-session read and reload readback. Final results
and numeric totals are in the machine evidence. Dollar costs are SDK-priced
estimates, not provider bills. No domain task or external message is created.

Seven turns meet five functional expectations and retain two failed expectations;
six runs deliver and the pre-fix clock run fails. Thirteen model requests report
7,157 tokens and USD 0.015844 estimated cost, with zero unknown usage. All seven
effects are terminal: five completed and two failed, each with one terminal event.
Both corrected clock answers match the exact seconds/offset of their durable tool
results. Recovery refreshes only usage.last_updated on the failed predecessor;
all its other fields remain exact. Cold restart comparisons before follow-up and
the final reload comparisons are byte-equivalent for Interaction/Conversation
records. This distinction is retained in the machine comparisons.

## Verification and limits

Sixteen channel cases cover exact policy/deny precedence, malformed/duplicate
dormant declarations, forced SDK bypass, no eligible skill, host asset binding and
independent permission on WhatsApp/custom channels. Five clock regressions add
None support while rejecting False, zero, empty list/dict without reading App.now.
The affected channel/clock slice passes 30. Final native/host/real-PG/Docker/clock
lanes pass 752 on each Python 3.11/3.12; the migration/scaffold slice passes 181.
Publication checks remain separate and are recorded with PR/commit evidence;
earlier 734/738 lanes precede the clock fix and are not final-source evidence.

An unmanaged mypy run reports nine callback.py/handoff_action.py errors. Shadowing
changed runtime files with their parent versions reproduces exactly those nine;
the pinned repository all-files hook remains the publication gate. Unrelated
files are untouched.

The reference retains nine migration blockers. Legacy channel overrides, voice
limits/prompts/pins, lock_active_flow, vision artifacts, WhatsApp formats/sends,
model-service callers and retained tasks still need migration. Inactive members
receive structural projection, with full official-loader/runtime qualification
deferred until eligible and remaining an offline cutover gate. Cross-channel
conversation reuse, authenticated adapter origin, prior workspace assets,
external effects and broader provider/deployment/quality evaluation remain open.
The stale-denial model reply remains a documented limitation. Enterprise,
migration-completion and performance-improvement claims remain unproven.
