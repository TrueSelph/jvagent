# Native tool scheduling and migration qualification

## Change and acceptance contract

Preserve the legacy one-tool concurrency ceiling through the public Pydantic AI
execution mode. Keep the existing native parallel default. Refuse invalid modes
before model requests, isolate concurrent runs, restore the ambient SDK mode on
failure/cancellation, and inherit scheduling through official SubAgents. Wider
numeric legacy ceilings must remain blocked until an exact bound is qualified.
No JV dispatcher, semaphore or second execution engine is introduced. Sources:
`jvagent/harness/native.py:602`, `:635`,
`jvagent/harness/model_settings.py:7`, `jvagent/cli/harness_migrate.py:391`.

See [configuration and semantic differences](../runbooks/native-tool-execution.md).
The mapper preserves both explicit zero/one and the omitted legacy default;
multiple emitted calls now execute serially within SDK execution rather than
being drained across legacy ticks. SDK request/tool/usage limits remain in force.

## Browser and persisted-state evidence

Parent revision `e07139c0`; exact runtime Git blobs and the reviewed synthetic MCP
fixture blob are in [the smoke record](2026-11-06-native-tool-mode-smoke.json).
The disposable fresh PostgreSQL app on backend 8159 and jvchat 3140 contains two
agents. The serial declaration was produced by the actual offline converter from
a legacy one-tool declaration. The parallel comparison explicitly changes the
generated native mode; it is not presented as a wider-ceiling migration.

Actual GPT-4.1 requests asked each agent to look up ALPHA, BETA and GAMMA. Both
models emitted three tool calls in one response, and both delivered the correct
room/start values with visible three-tool activity and usage. The MCP fixture
uses a fixed 0.35-second asynchronous delay and independently records starts and
ends; acceptance is actual overlap, not UI event ordering.

| Mode | Peak actual active calls | Tool batch span | Outcome |
|---|---:|---:|---|
| sequential | 1 | 1.147478 s | All three correct |
| parallel | 3 | 0.387934 s | All three correct |

These are one synthetic batch per mode, not a throughput, p95 or general
optimization benchmark. Per-Interaction wall time is retained separately.
Each turn used two provider requests and 875 reported tokens. Total: four
requests, 1,750 provider-reported tokens, USD 0.005012 estimated cost and zero
unknown-cost calls. Per-call receipts reconcile to each persisted conversation
spend counter. Both SDK MCP processes exited after execution.

After backend restart, both modes persisted on the actual Action nodes. Browser
restoration retained both completed replies/tool groups/usage, two Interactions
and two SDK run registrations with no row/spend changes or additional model work.
No domain tasks were created. [Browser observations](2026-11-06-native-tool-mode-browser.json)
and the SQL/audit projections are retained. A fresh read-only reference-app
reassessment has 15 blockers, down from 16; the mapping closes only the reviewed
single-tool ceiling.

## Regression gates and remaining work

Nineteen initial regressions fail on the parent and pass after implementation.
The final 16 scheduling cases pass on both Python 3.11 and 3.12, including actual
three-call overlap, concurrent scope isolation, invalid configuration/startup,
failure/cancellation restoration and official child-mode inheritance. The latter
are deterministic SDK tests, not actual-provider child/cancellation browser
qualification. The affected CLI/runtime slice passes 124 cases on Python 3.11.
The native/host/real-PostgreSQL suites pass 575 cases per Python version; two child
cases added after collection pass in the separate final scheduling slices.
This is not a claim of a single 577-case full run. Source:
`tests/harness/native/test_tool_execution_mode.py:1`,
`tests/cli/test_harness_migrate.py:107`.

Staged all-files checks and the normal full-suite pre-push hook are separate
publication gates. Parent `e07139c0` has all nine remote checks green; new-head
checks are separate. Exact wider numeric limits, distributed capacity, blocking
adapters, mutation ordering across sessions, actual-provider nested scheduling,
remaining compatibility, retained-state migration, legacy removal and enterprise
release remain open.
