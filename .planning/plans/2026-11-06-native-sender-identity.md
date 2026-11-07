# Native channel sender identity

Status: local text-adapter sender boundary qualified; native sends remain gated.
Parent `c845a87e`; exact runtime hashes and per-turn results are in the
[machine evidence](2026-11-06-native-sender-identity-smoke.json), with
[browser observations](2026-11-06-native-sender-identity-browser.json).
This implements the first boundary from the [WhatsApp audit](2026-11-06-native-whatsapp-boundary.md).

## Ownership and behavior

`jvagent/tooling/channel_identity.py:25` provides a small host service for
authenticated adapter facts. It grants no permission and runs no tools. The
adapter binds agent, sender, exact channel, optional pre-existing session and a
weak reference to its live visitor. Public HTTP visitors cannot be promoted.
Proofs cannot be transferred to another visitor or restored from model/request
data. The live reference bounds their lifetime; this is not a wire credential.

`jvagent/action/interact/interact_walker.py:92` declares runtime-only private
state. `jvagent/tooling/context.py:24` snapshots it with the existing immutable
dispatch identity so changing/removing it after admission fails the existing
visitor-scope check. No schema, durable credential, signing protocol, database,
provider dependency, intent router or execution loop is added.

`jvagent/action/whatsapp/utils/endpoint_helpers.py:155` retains the sender even
when reusing a conversation, then binds the proof before spawning. Authentication
remains at the existing webhook boundary (`jvagent/action/whatsapp/endpoints.py:548`).
Trusted deferred transport is a host boundary, not an assertion read from the
message body; the pinned jvspatial internal HTTP route separately requires its
configured secret unless loopback trust is explicitly enabled
(`jvspatial/api/deferred_invoke_route.py:47` in installed 0.2.4). This source
inspection does not qualify Lambda/LWA or serverless deployment.

`jvagent/action/whatsapp/whatsapp_action.py:1527` requires the proof on native
dispatch. Channel names remain an open routing set. Existing legacy execution
retains its old gate during migration; no native fallback is added. Voice/public
callers without a qualified adapter proof receive
`whatsapp_verified_sender_required`. Existing four WhatsApp tool declarations
remain refused by native composition: this increment does not enable sends or
replace their missing acknowledgement contract. Named tool authorization remains
independent of sender provenance.

The generic JV service owns ingress facts only. Official Pydantic AI/Harness
continues to own skill loading, dispatch, effects and execution. Keep this host
adapter while JV owns channel ingress; remove any duplicate upstream integration
if a qualified SDK host-identity interface replaces it. The transitional legacy
gate and executor retire at the existing migration/deletion gate.

## Qualification

Fourteen sender tests cover immutable facts, absent/forged proof, public identity,
agent/sender/channel/session changes, another visitor, proof removal/rebinding,
and real walker creation/bootstrap with new/reused sessions. The initial suite
caught a missing comparison to the live visitor agent ID; it was corrected.
Removing native enforcement makes both claimed-channel regressions fail. No
mutated source remains. Complete WhatsApp/admission/host-context slices pass
228 on each Python 3.11/3.12. Native/host/real-PostgreSQL lanes with configured
Docker tests pass 742 on each supported Python version. Required all-file hooks
pass; the tracked Action snapshot is regenerated and reviewed.

The disposable PostgreSQL app on 8171 and owned provider stub on 8172 use GPT-4.1.
Seven actual model turns include ordinary arithmetic/default-channel diagnostic,
fresh public text/voice channel claims with forged payloads, a valid signed
Meta-shaped text webhook and a new inbound message after cold restart. Invalid
signatures return 401 before execution. Both signed turns produce a fresh SDK
tool result with `admitted:true`; public claimed channels produce
`admitted:false`/`whatsapp_verified_sender_required`. The diagnostic itself
performs no provider call. Existing response-bus traffic is accepted by the
loopback stub only; nothing is sent to Meta or a person.

Two existing-session diagnostic requests answered from history without invoking
the tool; both remain failed expectations. Fresh requests exercise the boundary.
The native chat shows ordinary output, negative diagnostics, tool activity and
usage. API-created public sessions were added to the local sidebar as pointers
only; the client read persisted replies without fabricated transcripts. Signed
webhooks were invoked in the browser and their actual positive tool results were
verified through SQL; a customer-facing WhatsApp UI is not claimed. Initial
404/CORS/HTTPS-policy fixture mistakes are retained in the browser record. The
local webhook probe uses trusted-loopback forwarded HTTPS, not real TLS.

All seven runs are closed with local harness status `delivered`; all five effects
have one matching completion event. Four conversations reconcile 12 requests,
5,209 provider-reported tokens and USD 0.012134 estimated cost, zero unknown
usage. No task is created. Cold restart and reload preserve Interaction and
Conversation rows exactly; the new trusted turn reuses its session and preserves
all earlier rows. Local `delivered` is not recipient delivery acknowledgement.

## Remaining work

Implement reviewed native reads/sends and durable acknowledgement/uncertain-send
semantics, then canonical WhatsApp skills and channel-policy migration. Qualify
jvvoice origin, real Meta/jvconnect, serverless ingress, delivery recovery and
deployment separately. No reference blocker is removed by this foundation alone.
Two stale model responses also remain an evaluation limitation. Retained-state
cutover, old-engine deletion and enterprise qualification remain unfinished.
