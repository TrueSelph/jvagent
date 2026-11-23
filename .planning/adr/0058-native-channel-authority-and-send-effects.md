# ADR-0058 — Native channel authority and send effects

- Status: Accepted; qualification is limited to the recorded local text profile
- Date: 2026-11-06
- Supplements: [ADR-0057](0057-native-declared-egress.md)
- Governing direction: [native rebuild](../plans/2026-11-04-harness-rebuild-execution.md)

## Context

A request channel is routing metadata. The WhatsApp audit found a channel-only
sender gate, success without provider message IDs, and unverified form JSON
treated as business completion. Native reply publication also forwarded request
data into adapter metadata, including raw-send escape hatches. Findings and
regressions are in [the qualification record](../plans/2026-11-06-native-whatsapp-tools.md).

## Decision

JV owns authenticated ingress identity, Action policy and channel transport;
Pydantic AI and official Harness own execution, skill loading, dispatch and
execution records. No second execution engine, state database or retry loop is
introduced.

Authenticated adapters bind sender facts to one live visitor. The existing graph
StepStore projects matching host facts into official SDK run registration metadata
(`jvagent/tooling/channel_identity.py:76`, `jvagent/harness/step_store.py:27`).
Public HTTP native replies are local publications. Request data is not copied
into native reply metadata; explicit server metadata controls publication
(`jvagent/action/native_harness/native_harness_action.py:90`,
`jvagent/action/reply/reply_action.py:382`,
`jvagent/action/response/response_bus.py:470`). A public channel or metadata claim
does not authorize an external send.

The WhatsApp adapter verifies SDK registration scope before transporting native
output. A nonexecuting admission/recovery notice has no SDK run; it may use live,
matching adapter proof for its immediate publication. Retained messages cannot
recreate that live authority (`jvagent/action/whatsapp/whatsapp_adapter.py:94`).
This does not create a durable outbox or qualify delivery replay.

Native catalog tools require verified sender scope and named host permission.
Sends additionally require an official SDK effect annotation before I/O, a
host-chosen idempotency key and one bounded provider message ID. A provider ID
acknowledges acceptance, not recipient delivery or business completion
(`jvagent/action/whatsapp/native_tools.py:39`,
`jvagent/action/whatsapp/utils/message_receipts.py:6`).

The default mutation key remains compatible with existing run/call/resource keys.
Reviewed domain adapters may override its derivation. WhatsApp keys bind the
Interaction, Action, sender and normalized request, excluding the generated Flow
tracking token. Options ignored by the encoder do not produce another identity.
The graph adapter rejects another call/run claiming the same key before a second
body is dispatched (`jvagent/harness/mutation_tools.py:52`,
`jvagent/harness/native.py:141`, `jvagent/harness/step_store.py:144`). This is
single-writer enforcement, not distributed fencing or provider-wide exactly-once
delivery. A duplicate fails closed; it does not fabricate another success receipt.

Missing/malformed acknowledgements, provider failures and duplicate claims require
reconciliation. SDK mutation facts remain intact and block blind accepted-input
replay. Named permission denial before I/O can be followed by a fresh request
after the operator restores permission; the recorded browser case demonstrates
that distinction.

## Compatibility and remaining gates

Preserve the four tool names, schemas, Action lifecycle and existing domain APIs.
Require exactly one Flow selector to close the allowlist ambiguity. Invalid
receipts no longer report success, including through transitional legacy callers
(`jvagent/action/whatsapp/whatsapp_action.py:78`, `:1730`, `:1930`, `:2021`).
The three skills use canonical hyphenated names; authored selectors must change
explicitly. Their old persisted task/configuration owners are not auto-migrated.
Retire service-flow `always-active` pinning into official discovery/loading.
Submitted JSON cannot prove signup, authentication or booking completion.

The text profile uses actual GPT-4.1 calls and an owned loopback provider stub.
It does not qualify real Meta/jvconnect, TLS termination, jvvoice caller proof,
serverless deployment, remote/background delegation writes, provider alias
equivalence, distributed effect ownership or uncertain-send operator recovery.
Public HTTP channel bridges require a qualified authenticated adapter rather than
metadata tunnelling. Existing legacy transport remains transitional until cutover.

Keep identity projection and domain key policy as thin JV services. Use public
SDK records and hooks for execution; remove any upstream-gap adapter when a
qualified upstream interface replaces that responsibility. Legacy execution,
state/configuration migration and enterprise release gates remain open.
