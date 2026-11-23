# Native MCP read cohort and fast-failure delivery

Status: functional development cohort; full MCP/release qualification remains open.
Parent revision `952079a5`; branch `feat/pydantic-harness-rebuild`.
[Machine readback](2026-11-05-native-mcp-smoke.json) records working-tree source
blobs, all eleven browser attempts, timestamps, messages, usage and graph records.
[UI observations](2026-11-05-native-mcp-ui.json) preserve visible responses/events.

## Ownership and scope

MCPAction retains its declarative server configuration. Its native adapter uses
public Pydantic `MCPToolset` and FastMCP `Client`/`StdioTransport`; the library owns
protocol, schema validation, tool execution and connection cleanup
(`jvagent/action/mcp/mcp_action.py:886`). Native ingress consumes that surface
without calling the old MCP `fulfill` selector or client wrapper
(`jvagent/action/native_harness/native_harness_action.py:165`). Existing ordinary
Action tools still use their existing `get_tools()` declarations.

Install `.[native-harness,native-mcp]` for this profile. The optional extra pins
`fastmcp-slim[client]==4.1.6`; non-MCP native apps do not need it
(`pyproject.toml:50`). The base MCP SDK is consistently pinned to `2.3.0` in
project/requirements/Action declarations, and the bundled filesystem service now
uses its public `MCPServer` class (`jvspatial_fs_server.py:209`). A real stdio test
reads a file under the configured prefix; this does not qualify filesystem writes.

Declare `native_read_tools: [mcp_records__lookup_record]` in the MCPAction context,
with a plain-stdio `records` server and `sandbox_mode: false`. Author the same name
in the selected skill's `allowed-tools` and, when essential, `requires-tools`.
Only names present in both host review and skill allowlist are exposed. Remote
`readOnlyHint` cannot authorize an unreviewed tool. The wrapper checks unchanged
host context, rejects model authority fields and checks access at every call
(`jvagent/harness/mcp.py:35`). Server allow/deny selectors remain binding.

This is an explicitly reviewed shared read service. HTTP/auth, per-user filesystem
profiles, sampling/elicitation, background MCP tasks, write tools, delegated MCP,
MCP research provenance and arbitrary remote-server output remain unqualified.
Background task selection and remote server instructions are disabled/rejected;
research output policies reject these toolsets pending provenance qualification
(`mcp_action.py:960`, `harness/mcp.py:43`, `harness/native.py:136`). A host read
classification is a trust decision, not proof that a server has no side effects.

## Confirmed defects and repairs

- Legacy MCP closures accepted `sn`/`tn` model arguments that could override the
  checked server/tool. Routing now lives in a factory closure; those fields, if
  legitimate schema arguments, reach the chosen tool as data. Sanitized-name
  collisions reject. Regression first failed before the fix. The legacy engine
  receives this security repair, but native MCP does not use that engine.
- The initial Action-client native experiment completed reads but left one stdio
  subprocess per turn. That path was discarded. Public SDK lifecycle tests and
  browser process readback confirm subprocess exit after native success/error.
  The two experimental processes were explicitly stopped; records remain.
- Pre-model admission errors escaped native ingress without a user notice.
  Native ingress now persists `blocked/admission_failed` and publishes a literal,
  non-sensitive notice (`native_harness_action.py:106`). Existing-run failures and
  cancellation retain their existing recovery/delivery behavior.
- A real notice was saved but invisible because SSE subscribed after a fast turn
  published. Subscription can now replay the existing bounded session queue for
  the exact server-established Interaction. Its synchronous queue callback makes
  replay precede further live events (`response_bus.py:1335`,
  `interact/endpoints.py:1076`). The wire regression excludes another turn/caller.
  This is not durable reconnection or distributed delivery recovery.
- Pinned StepPersistence leaves a started effect when core returns a tool retry
  signal, which bypasses `on_tool_execute_error`. Two real-protocol regressions
  first failed. `ReadRetryReceipts` closes only explicitly reviewed read attempts
  by invoking the library’s public `on_tool_execute_error` from
  `wrap_tool_execute`, then rethrows the native signal (`harness/read_retries.py:12`). It never repeats tools, settles writes or
  guesses cancellation outcomes; failed persistence aborts. Remove it when the
  pinned library terminalizes these retries and the same regressions pass without
  it. Earlier disposable records are retained rather than rewritten.

## Browser and graph evidence

Disposable PostgreSQL database `jvagent_native_mcp`, API 8134, jvchat 3134, separate
browser context and disposable administrator; existing user deployments unchanged.
The synthetic MCP service is `tests/harness/native/mcp_fixture.py`.

Eleven attempts were retained, including the original blank result, discarded-client
reads, the saved-but-invisible notice, corrected visible notice, successful SDK
reads and errored reads. Eight model-backed turns used 16 actual OpenAI requests,
7,152 reported tokens and USD 0.016374 estimated; recorded unknown-cost count zero.
These are request receipts, not invoice reconciliation or an efficiency benchmark.

Final visible notice: configuration prevented this request from starting; no
provider calls or tool effects. Corrected lookup: Cedar / 14:30, 710 tokens,
USD 0.001696 estimated, 4.826468 seconds backend Interaction duration. Missing
record: explicit failure, 886 tokens, USD 0.002012 estimated, 3.216613 seconds.
After the receipt adapter, UNAVAILABLE-88 remains unavailable: 1,048 tokens,
USD 0.002300 estimated, 5.884286 seconds; persisted tool effect `failed`, end time
present, no live fixture process (PID 24414). A final RETRY-LEDGER-99 run through the public error hook
records both `failed` and `tool_call_failed`: 1,214 tokens, USD 0.002674 estimated,
6.035822 seconds, with subprocess exit confirmed. Tool events and usage were visible.
The selected conversation was readable after browser reload; the existing bundle
still carries earlier messages visually across New Conversation. Graph sessions
are distinct. Fresh-client-build isolation reproduction remains a separate gate.

A fresh Python 3.11 native/MCP/read-profile install passed dependency consistency:
core 2.66.0, Harness 0.66.0, MCP 2.3.0, FastMCP client 4.1.6, OpenAI 3.46.0,
jvspatial 0.1.1, 105 unique distributions. The prior native read profile had 93;
these are qualification environments including tests/read dependencies, not a
production footprint comparison. Python 3.12 retains OpenAI 3.45.0; it is a distinct
qualified environment. Unified legacy/native dependencies remain an open gate.

## Repository checks

Native/profile/WebFetch/real PostgreSQL suites: 195 passed on Python 3.11 and
3.12, with 121 warnings each (27.31s / 28.64s). The read-boundary/MCP slice passed
46 tests; the final transport/MCP/report slice passed 125 tests.
Final all-files hooks, the affected slice rerun and normal full-suite push hook
remain mandatory before this milestone is reported pushed. Earlier nine green
remote checks apply to parent `952079a5`, not this new candidate.

## Acceptance limits

Actual browser evidence covers administrator read success/error, configuration
failure and process cleanup. Deterministic protocol tests cover access denial,
missing remote tools, remote read-hint non-authority, scope mutation, malformed
review declarations, read receipt settlement, write/cancel preservation and
storage failure. Native PostgreSQL suites and transport slices are separate gates.
Ordinary-member browser isolation, MCP delegation, timeout/cancel process faults,
HTTP/auth, multiworker recovery, migration, deletion and release evaluation remain
open. No enterprise or full MCP compatibility claim follows from this cohort.
