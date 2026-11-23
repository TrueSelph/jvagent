# WhatsApp native migration: boundary audit and implementation order

Status: offline assessment and admission regressions, not runtime qualification.
Examined revision: `9f7dfbf904a8ada642713f1f74a982adc29f0777`.
The [machine record](2026-11-06-native-whatsapp-boundary-audit.json) records ten
direct-Action probes, four native admission probes and source hashes. Provider
methods were replaced with local async stubs: zero network requests, model calls
or messages to people. A synthetic public-identity marker tests what the Action
actually checks; it does not reproduce the HTTP identity resolver or establish
an exploitable public endpoint.

## Confirmed findings

| Finding | Evidence and consequence |
|---|---|
| Channel is routing metadata, not sender proof | `jvagent/core/channel.py:6` explicitly permits caller-selected channels. Public ingress forwards channel into the walker at `jvagent/action/interact/endpoints.py:813`. The send gate at `jvagent/action/whatsapp/whatsapp_action.py:1505` checks channel and a nonempty user ID, not authenticated provider origin. Both sends refuse `default` but call the provider stub under `whatsapp` and `whatsapp_call`, including with an unproven public marker. These probes establish an insufficient domain boundary, not HTTP exploitability. |
| Missing provider receipt still reports success | Template/Flow sends default to an empty message ID and return `ok:true` at `jvagent/action/whatsapp/whatsapp_action.py:1710` and `:2000`. Both probes reproduce this for a provider envelope containing only `ok:true`. Native effects must distinguish acknowledged acceptance from an unknown outcome. Provider acceptance must not be described as recipient delivery. |
| Provider refusal is ordinary tool text | Both sends return JSON `ok:false` for the synthetic provider rejection, while `Tool.call` reports `is_error:false` (`jvagent/tooling/tool.py:72`). This is an existing string-result contract; globally reinterpreting all JSON would break modular tools. A reviewed native send adapter must validate its own domain envelope before claiming a completed mutation. |
| All four legacy tools currently fail native admission | `jvagent/harness/native.py:238` admits explicit read tools or qualified mutation adapters. All four legacy WhatsApp declarations lack native effect metadata. The probes refuse each one, even with a canonical eligible skill and permissive access policy. Preserve this fail-closed gate; do not mark sends as reads to make migration pass. |
| Form text can prompt unsupported completion claims | `examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/whatsapp_service_flows/SKILL.md:81` directs confirmation of signup from raw submitted JSON. This is a source finding, not a reproduced model response. A transport submission does not prove business completion; require the domain service result before claiming signup/booking success. |

Current text ingress verifies the Meta signature or bridge API key before
processing (`jvagent/action/whatsapp/endpoints.py:548`). The walker helper sets the
channel but carries no explicit provider-origin proof (`jvagent/action/whatsapp/utils/endpoint_helpers.py:130`).
Voice establishes the caller/session before handing control to jvvoice
(`jvagent/action/whatsapp_voice/whatsapp_voice_action.py:285`); the HTTP return path
needs an explicit authenticated contract rather than trusting its channel name.
Do not infer text or voice provenance from an arbitrary dictionary in `data`.

## Implementation sequence

1. **Establish provider sender provenance at ingress.** Reuse the existing
   server identity and signed-host-context services where their scope fits
   (`jvagent/core/host_context.py:116`). Text adapters promote sender facts only
   after webhook authentication. Bind agent, channel, provider sender and active
   interaction/session; validate again at dispatch. For voice, qualify the
   authenticated jvvoice callback contract, expiry and replay handling. A web
   caller selecting `whatsapp` must receive no send authority. Keep custom
   channels valid. A trusted adapter's source proof does not grant named tool
   permission. Do not add a parallel authentication or execution engine.
2. **Adapt the existing four tools explicitly.** Preserve names, argument
   schemas, allowlists and recipient binding. Declare the two catalog methods as
   reviewed reads. Adapt sends with `NativeMutationTool`, a named permission,
   visitor scope, an SDK-persisted effect before I/O and non-retryable semantics
   (`jvagent/harness/mutation_tools.py:26`, `jvagent/harness/native.py:137`). Validate
   domain responses locally. Keep the legacy envelope compatibility until its
   callers are migrated. A best-effort Interaction annotation is insufficient
   acknowledgement persistence (`jvagent/action/whatsapp/whatsapp_action.py:1770`).
3. **Define send ambiguity before enabling sends.** Persist provider message ID
   on acknowledged acceptance; do not claim recipient delivery without a delivery
   event. Missing receipts, timeout, cancellation and persistence failure must
   preserve uncertain external effects and block blind replay. SDK tool-call
   identity alone cannot deduplicate repeated user/model send intent. Qualify
   provider-supported reconciliation/deduplication separately; use the existing
   jvspatial graph and recovery services, never a second state database.
4. **Migrate the three skills together.** Canonical hyphenated names, explicit
   selector/cross-reference updates, existing schemas and channel eligibility.
   Replace `always-active` pinning with official skill discovery/loading and
   concise domain SOPs. Remove the raw-JSON business-completion assertion. Map
   channel-specific limits, prompts and tool policy explicitly; do not silently
   discard `channel_overrides` or add a second intent router. Document retained
   task compatibility before changing persisted owners.
5. **Qualify then cut over.** Real GPT-4.1 in-browser tests against a disposable
   app/graph, with an owned provider stub that never sends to people. Cover text
   and voice-origin admission, spoofed channel/body, sender/session mismatch,
   expired/replayed proof, permission revocation, forbidden recipient arguments,
   allowlist errors, provider refusal, missing receipt, crash after acceptance,
   duplicate attempt, submitted untrusted JSON, restart and unchanged reload.
   Observe SDK effects, visible tools/replies, graph readback and reconciled
   actual/estimated/unknown usage. Actual Meta/jvvoice interoperability and human
   delivery remain separate gates; a stub cannot qualify them.

## Validation and next gate

`tests/harness/native/test_whatsapp_admission.py` adds four regressions against
the actual legacy declarations and native composition root. Removing the native
qualification guard must make all four fail; restore the source byte-for-byte
after the mutation check. The complete WhatsApp slice plus these regressions
passes 196 cases on each Python 3.11/3.12; all 14 offline probes repeat on both.
These tests protect unadapted tools even after a native
adapter is added. They do not assert that legacy unsafe behavior should persist.

This assessment changes no runtime, skills, provider configuration or personal
application. It does not claim a new browser round. The current nine reference
migration blockers remain open; WhatsApp sender provenance and acknowledgement
semantics are prerequisites inside those blockers, not additional completed work.
Proceed with ingress provenance before native send admission, then run the full
runtime/browser qualification above. Migration, deletion and enterprise gates
remain active under the [execution contract](2026-11-04-harness-rebuild-execution.md).
