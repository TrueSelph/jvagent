# Native host-policy validation, durability and private egress

Parent `5739cf5d`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent CI checks passed. Governing [execution contract](2026-11-04-harness-rebuild-execution.md),
[graph/process receipts](2026-11-05-native-host-context-smoke.json), and
[sanitized browser requests and visible UI](2026-11-05-native-host-context-browser.json).

## Confirmed defect and change

The parent discarded an invalid host envelope but continued a non-empty native
request as an ordinary turn. A real browser request with a confirmed malformed
envelope reached GPT-4.1, returned HOST-CONTEXT-VALIDATION-CHECK, and persisted a
delivered result: one request, 428 reported tokens, USD 0.000922 estimated.
This was a host-policy downgrade, not proof of forged context becoming trusted.

Native bootstrap now refuses an explicitly supplied host envelope unless it is
verified and consumed, regardless of utterance length. Omit the field for an
ordinary request; an explicit null is rejected. Caller/scope, signature, expiry,
request matching, replay and nonce-storage protections stay in the existing host
service. No new engine, signer protocol or execution store was introduced
(`action/interact/interact_walker.py:461`, `core/host_context.py:203`).

A second regression exposed nonce durability when deferred batching is enabled:
`save()` could return without committing the nonce. The host service now strictly
flushes the Conversation under its existing lock before promoting policy
(`core/host_context.py:269`). Fresh-context tests with batching on/off prove the
commit happens before Interaction/model execution. Flush failure rejects policy;
three of four durability cases failed before this fix.

The common outcome is `invalid_host_context`. HTTP streaming and embedded streaming
return a safe, specific error; non-streaming HTTP returns validation error 422
(`action/interact/endpoints.py:808`, `:1039`, `embed/interact.py:514`). No Interaction
or native execution is started for a rejected envelope. Explicit transitional
legacy behavior is unchanged pending migration/deletion.

Inspection of the actual signed response found a third defect: reply metadata
echoed the host envelope, including its signature and policy body. ResponseBus
now removes reserved host-policy data and its associated request ID before filters,
queues, subscribers or adapters receive a message (`response/response_bus.py:517`).
Four transport-mode/owner regressions failed before this fix. The final signed
model run returned PRIVATE-HOST-CEDAR with empty public metadata; unrelated
application run IDs and routing metadata remain compatible. Private fields in
the earlier captured responses are redacted, with explicit finding flags retained.

The new embedded rejection test also exposed the fixed five-second admission wait:
even an already completed rejection waited five seconds, while a queued turn could
be declared failed before acquiring its conversation lock. Embedded admission now
observes task completion/disconnect like HTTP instead of guessing that deadline
(`embed/interact.py:478`). A real 5.3-second queued-admission regression passes;
temporarily restoring the old wait makes it fail (one failure in 5.61s). Host
transport heartbeats, timeout ownership and provider-backed cancellation still
need qualification; this change does not establish their semantics.

## Actual browser and graph results

A fresh disposable app/database/browser context uses API 8136, jvchat 3136 and
`JVAGENT_INTERACT_PUBLIC_AUTH=required`. Its dedicated signing key stays in the
host fixture. A one-shot browser fetch wrapper adds fixed synthetic envelopes to
normal jvchat submissions; the temporary loopback payload server was stopped after
the tests. No backend execution function was patched. Request signatures, signing
keys, bearer credentials and session tokens are omitted from committed evidence.

- A valid signed request returned VERIFIED-HOST-CEDAR, a value supplied only by
  signed host policy. Graph readback has one consumed nonce whose server correlation
  matches the native SDK run. That ledger survived subsequent execution.
- Replay while the envelope was still unexpired, foreign agent/user/session,
  expired context, changed signature, mismatched request ID, explicit null and
  malformed context all returned `invalid_host_context` through the real UI.
  Preconditions in the receipt prove the foreign-scope envelopes were validly
  signed and unexpired, rather than failing accidentally for expiry.
- Those ten streaming rejections (including deferred-mode replay) completed in 118.6–165.4ms of browser fetch time.
  A corrected non-streaming malformed request returned 422 with the same reason
  (53.9ms). Rejection created no additional Interactions, tool effects or usage
  receipts. Native execution did not start; no provider receipt is expected.
- An ordinary MCP lookup after all rejections returned Cedar / 14:30 with visible
  call/result activity and a completed effect, demonstrating continued service.
  The seven model-backed fixture turns are closed/emitted/delivered. Eight requests
  used 3,096 reported tokens and USD 0.006732 estimated. Including the parent defect
  probe, the round used nine requests, 3,524 tokens and USD 0.007654 estimated.
  These are functional samples, not a performance comparison or provider invoice.
- After a backend restart with deferred writes enabled, a second signed policy
  returned DEFERRED-HOST-CEDAR. Its nonce was committed, bound to that SDK run and
  rejected on unexpired replay. Expired entries were pruned; all four phase readbacks
  (immediate writes, deferred writes, private egress and log-private egress) are retained without raw
  nonce values.
- The module observer's sampled heartbeat remains live with no legacy runtime,
  executor or loop after final publication.

Two fixture mistakes are retained: an unsupported automation argument failed to
arm the first signed-context attempt, so its ordinary confirmation and cost do not
count as signed-policy proof; the first non-streaming probe included forbidden
agent_id in its body and failed schema validation before host-policy validation.

## Regression and qualification scope

Eleven new native rejection cases failed on the parent before the guard change;
the ordinary-request control passed. Coverage includes malformed/null/signature,
expiry, foreign scope, request mismatch, missing signing key, nonce-store failure
and replay with non-empty text. Four additional durability cases cover batching
on/off and strict-flush failure. Five publication tests cover envelope removal
across native/legacy and streaming/non-streaming modes while preserving unrelated
application metadata. The same public projection removes the envelope from
interaction log data; a separate regression first failed before that logging fix
(`core/host_context.py:24`, `interact/webhook_pipeline.py:165`). A final fresh
signed-model request after the shared filter change returned LOG-PRIVATE-HOST-CEDAR
with clean metadata. Stable HTTP/embed error tests and queued-admission
coverage supplement the existing HMAC, access-denied-before-consumption, isolation,
accounting and cancellation tests. Final checks and push state are tracked in the
[progress record](2026-11-04-harness-rebuild-progress.md) and draft PR #260.

Qualified here: administrator signed-host policy success and these rejection cases
on an explicitly authenticated local deployment. Not qualified: ordinary-member
and foreign bearer/session capability isolation, popup messenger, actual-provider
embedded flows/cancellation, distributed ownership and durable delivery recovery.
Development public auth defaults off and unknown modes also resolve off
(`action/interact/session_token.py:64`); those configurations and their migration
are not covered by this cohort. They remain a release policy gate. Interview
execution, full compatibility, migration, legacy deletion and workload/release
evaluation remain open. The goal is active; no enterprise qualification is claimed.
