# Native cumulative output-limit failure and explicit retry

Parent revision: `2fab70ee`. This increment changes no execution behavior.
It qualifies the existing public output-limit mapping and documents why the
supported deprecated spend callback cannot yet be removed safely.

## Actual-provider browser and PostgreSQL evidence

Isolated fixture: `/private/tmp/jvagent-native-limit-smoke`, PostgreSQL database
`jvagent_native_limit_20261105`, backend 8139, source-client preview 3136.
The [failure export](2026-11-05-native-limit-failure-smoke.json),
[retry export](2026-11-05-native-limit-recovered-smoke.json) and
[browser receipt](2026-11-05-native-limit-browser.json) record source blobs,
runtime module observation, visible responses, tool events and persisted state.

The first GPT-4.1 lookup ran with cumulative `max_output_tokens: 1`. A real
provider response exceeded that SDK allowance. The browser displayed the
configured-budget notice; the Interaction remained failed, closed and emitted.
Reported usage was 322 input + 24 output = 346 tokens, one request,
USD 0.000836 estimated. HTTP duration was 4.2703s; persisted turn duration
was 4.026003s. Model-boundary duration was explicitly unmeasured, not zero
latency: the limit prevented the ordinary completion timing event.

An authenticated administrator updated the graph Action allowance to 7000
through the existing settings API (HTTP 200, updated field, no skipped fields).
An explicit user retry in the same conversation then returned:
`REHEARSAL-47 is scheduled in room Cedar and starts at 14:30.`
The browser displayed `mcp_records__lookup_record`, two model attempts and
859 reported tokens. HTTP duration was 3.425s; persisted duration 3.279538s;
measured model duration 2.014s. Retry cost was USD 0.001988 estimated.
No backend restart occurred between failure and retry. The source fixture YAML
still has the intentionally tiny allowance; this tests a live graph settings
change, not a source-file migration.

Fresh PostgreSQL readback retains the original failed outcome, response and
346-token cost receipt. Its usage update timestamp advances during reconciliation;
the whole row is therefore not claimed byte-identical. The new run is delivered
and records recovery from the prior accepted-input frontier. Conversation totals
are three requests, 1205 reported tokens and USD 0.002824 estimated, with zero
unpriced requests and zero outstanding budget reserves. All dollar amounts are
estimates, not provider invoice amounts. The fixture MCP audit has one line,
`REHEARSAL-47`, across the two turns. There was no automatic paid retry.

## Upstream-gap adapter and regression

`jvagent/harness/accounting.py:33` documents the retained callback's removal
condition. Trying an immediate public `AbstractCapability.on_event` listener
caused 17 existing regressions because the pinned SDK selects streaming when
that listener exists. The experimental execution changes were reverted.
`tests/harness/native/test_spend_event_boundary.py:26` reproduces the protocol
change using public APIs: the same FunctionModel works without the listener,
while the listener receives its event but then requires a stream implementation.
No private dispatch hook or misleading capability override was introduced.

The second test at `tests/harness/native/test_spend_event_boundary.py:62`
proves that a cumulative output exception retains the public spend receipt,
estimated pricing, conversation ledger and persisted Interaction usage before
the usual terminal model snapshot. The test does not treat a fake supplied
cost as provider billing; it asserts the official pricing result and matching
ledgers. Both new tests pass.

Native/host/real-PostgreSQL suites: 369 passed on Python 3.11 and 369 on 3.12.
All-files hooks passed clean after the initial formatter rewrite was re-staged.
Final publication and exact-head CI are separate gates.

## Scope remaining

This qualifies cumulative SDK output exhaustion and explicit live-settings retry
for the tested OpenAI/MCP/local PostgreSQL profile. It does not qualify provider
per-request clipping, every provider, durable replay of failure notices after
client cache loss, distributed budget concurrency or provider invoice accuracy.
Failure `usage_complete` remains false and model timing remains unmeasured.
Migration's 31 reference blockers, historical-state upgrade/rollback, default
native cutover, legacy deletion and the broader enterprise gates remain open.
