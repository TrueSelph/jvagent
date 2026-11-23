# Native control retirement and response policy qualification

## Changes

The offline converter explicitly retires typed `block_raw_tool_invocation`
booleans and the `tool_servers: "-all"` selector. The native path already uses
SDK skill activation, reviewed MCP tool declarations and independent host access.
No old prompt/auto-promotion switch or second MCP selector is copied into the
native harness. Restricted/empty/invalid selectors remain blockers. Enabled MCP
Actions require a nonempty, named native review list even when the old selector
was omitted; explicit boolean `enabled: false` preserves an inactive Action.
Sources: `jvagent/cli/harness_migrate.py:77`,
`jvagent/action/native_harness/native_harness_action.py:188`,
`jvagent/harness/mcp.py:38`.

The browser negative case exposed a separate confirmed gap: GPT-4.1 correctly
said an unreviewed tool was unavailable, but an implicit legacy response rule
replaced the whole answer with a generic deflection. Native replies now select
declared rules using a host-bound persisted SDK registration. Both egress routes
share the selector; explicit rules and existing delivery services remain.
Legacy replies retain their existing defaults. This removes implicit old behavior
from the native path without adding another model call or a response regex.
Sources: `jvagent/action/response/policy.py:8`, `:48`,
`jvagent/action/reply/reply_action.py:38`,
`jvagent/action/response/response_bus.py:20`.
See [native response policy](../runbooks/native-egress.md).

## Actual-provider browser evidence

Parent revision `93f9e4e3`; [the smoke record](2026-11-06-native-controls-smoke.json)
binds tested runtime Git blobs, process epochs, MCP fixture, candidate reports,
SDK state digests, original-row hashes, receipts, spending and visible outcomes.
A fresh disposable PostgreSQL graph on backend 8160 and jvchat 3140 used actual
`openai:gpt-4.1`. Two legacy declarations differed in the raw-tool flag's boolean
value; both candidates were produced by the actual converter and hosted natively.

- Both explicit named lookup requests invoked the reviewed MCP tool once and
  returned the correct Cedar/14:30 values with visible tool activity and usage.
- The original unreviewed-name request made no tool call. Its correct model
  output became an unhelpful UI deflection; that delivered result is retained
  as a semantic presentation failure, not counted as request success.
- After the fix and backend restart, the same request produced the exact useful
  unavailable answer in both SDK output and visible response. It made no tool
  call. The independent MCP audit contains only the two permitted lookups, with
  no unreviewed-body marker. The last fixture processes exited.
- Reload restored the corrected reply and original history without model work,
  SDK registrations, row changes or spend changes. All three pre-fix records
  remain exact; the old failure is not retroactively repaired.

Four turns delivered; three satisfy their full request and one retained result
failed presentation. Six provider requests used 2,216 provider-reported tokens
and USD 0.005092 estimated cost, with zero unknown-cost calls. Each receipt and
conversation counter reconciles. Per-turn durations and request outcomes are in
the record; [browser observations](2026-11-06-native-controls-browser.json) retain
the original and repaired UI. No domain task or unreviewed tool invocation occurs.
The read-only [reference report](2026-11-06-native-controls-reference-blockers.json)
has 13 blockers, down from 15. A candidate is still not cutover authorization.

## Regression and publication gates

The initial CLI run reproduced 23 failures before implementation. Final affected
CLI/migration/MCP/skill suites pass 173 cases on each Python 3.11/3.12. The two
egress regressions reproduce the live rewrite on both original routes. Final
response/reply/parameter/egress slices pass 253 on each version, covering direct,
bus and streaming output, explicit policy, malformed policy, bare flags,
unregistered/duplicate runs and foreign host scope. Full native/host/real-PG lanes
pass 577 on Python 3.11 (111.47 seconds) and 3.12 (114.48 seconds). Sources:
`tests/cli/test_harness_migrate.py:1`,
`tests/action/response/test_native_policy.py:1`.

The first streaming test expected text in the final control envelope; the
existing protocol returns an empty final after publishing/persisting the content.
The assertion was corrected to verify that protocol and the actual persisted
response. No streaming runtime change was made for that fixture mistake.
Staged all-files checks and normal full-suite pre-push are publication gates.
Parent 93f9e4e3 has all nine remote checks green; new-head checks are separate.

## Remaining gates

The old implementations remain present pending migration/removal. Restricted MCP
selectors, other transports/writes, Action/skill parameter projection, clock
service/tool-tier parity and reasoning-display privacy remain unqualified.
The old core tier exposes `get_current_datetime`; it cannot be blindly retired
without preserving that service (`jvagent/action/orchestrator/core_tools.py:333`).
Remaining flow/channel/vision and skill compatibility, recovery, retained-state
migration, legacy deletion, evaluation and enterprise deployment stay open.
