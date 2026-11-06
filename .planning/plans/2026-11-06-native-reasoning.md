# Native provider reasoning presentation

## Change and ownership

The native host callback previously forwarded only tool lifecycle events. Public
SDK reasoning completion events were discarded, and the legacy visibility switch
blocked offline migration. The adapter now optionally forwards completed
ThinkingPart content from PartEndEvent through the existing response bus. No
prompt, reasoning generator, model configuration or execution loop is added.
Tool activity remains independent. See the
[presentation contract](../runbooks/native-reasoning.md) for defaults, boundaries
and source anchors. Public event semantics are documented by
[Pydantic AI](https://pydantic.dev/docs/ai/api/pydantic-ai/messages/).

`stream_reasoning_trace` maps to the literal boolean `publish_reasoning`; false is
preserved and malformed values block. The unchanged full reference declaration
now has eleven migration blockers, down from twelve. This closes the unknown
setting mapping only, not reasoning-provider qualification or reference cutover.
See [the read-only report](2026-11-06-native-reasoning-reference-blockers.json).

## Verification

Thirteen event regressions cover the public SDK's streaming path, opt-in/opt-out,
exact completed text, signature/opaque-metadata exclusion, empty parts, final-text
exclusion, tool independence, child/run attribution, per-part display bounds,
interruption before completion and invalid startup values. Eight migration cases
preserve both boolean values and reject non-boolean coercion. Sources:
`tests/harness/native/test_native_events.py:1`,
`tests/cli/test_harness_migrate.py:105`.

Final native/host/real-PostgreSQL lanes pass 665 cases on each Python 3.11 and 3.12,
including the reviewed Docker image/file-root profile. An earlier run omitted
those explicit test environment settings and passed 654 with five skips before
the final startup/interruption cases; it is not substituted for the final lanes.
The affected event/Action/migration slice passes 180. All-files checks pass after
the first black run reformatted the new test. Full pre-push and exact-head CI
remain separate publication gates. All nine remote checks passed on parent
`de92d6e9`.

## Real browser and graph evidence

[Machine evidence](2026-11-06-native-reasoning-smoke.json) binds the tested source
blobs and process epochs, receipt accounting, scoped SDK state, request durations,
tool traces, restart and reload audits. [Browser observations](2026-11-06-native-reasoning-browser.json)
retain tool visibility, exact replies, usage badges and restored transcript.

A disposable PostgreSQL app on backend 8164 and source jvchat 3140 uses the actual
OpenAI GPT-4.1 provider. With publication enabled, the clock skill calls
get_current_datetime and returns its exact timestamp/timezone. The browser shows
one tool call, two model attempts and 762 reported tokens. After a cold restart
with publication disabled, a fresh tool lookup returns a later timestamp, with
one tool call, two attempts and 1,032 reported tokens. Tool-result timestamps match
the visible replies. The second run's SDK history source names the first run.

The two turns reconcile four provider requests, 1,794 provider-reported tokens and
USD 0.004152 estimated cost, with no unknown-cost calls. There are two scoped SDK
run records embedded in the Interactions, not a separate registration collection.
Both are closed/delivered; only the clock read effect occurs and no domain task is
created. Restart leaves the first Interaction/spend unchanged. Reload and explicit
conversation selection restore both replies/tool disclosures/statistics without
changing either Interaction, SDK records or conversation spend.

Fixture failures remain explicit. An outdated noncanonical access-control Action
name first prevented bootstrap. A malformed fixture permission rule then denied
two browser requests before model work, leaving two empty zero-spend conversations
and no Interactions or SDK runs. Merge bootstrap preserved the persisted rule,
as designed; a privileged public graph update applied the already-qualified clock
policy before the successful cohort. The harness and provider were not patched.
The UI automation needed the actual composer change handler because the initial
DOM fill did not update React state. No successful request is inferred from the
failed sends.

## Limits and next gate

GPT-4.1 emits no reasoning text here. Actual-provider evidence therefore qualifies
absent-reasoning behavior and tool/history/accounting preservation for both
settings. Positive completion/display and interrupted-stream behavior use the
public SDK FunctionModel. Actual reasoning-capable provider, child/UI/cancellation,
channel and total-trace retention/capacity matrices remain open. The adapter does
not reveal opaque provider fields, reconstruct hidden reasoning, or generate a
substitute trace. No overall performance or enterprise qualification is claimed.

The reference configuration still requires the remaining eleven migrations;
stored-state cutover, rollback, legacy/pilot deletion and wider release gates
remain required by the full goal.
