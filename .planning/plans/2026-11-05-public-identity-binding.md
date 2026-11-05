# Native public caller binding

Parent `8673299c`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
This is an ingress-security increment under the [execution plan](2026-11-04-harness-rebuild-execution.md).
See [graph/process evidence](2026-11-05-public-identity-smoke.json) and
[sanitized browser probes, SSE and UI](2026-11-05-public-identity-browser.json).

Confirmed in the disposable auth-required app: an anonymous create with the
administrator's user_id created a conversation under that user and ran a model.
The unauthenticated session/open route also issued a capability for the existing
administrator conversation; that capability resumed it and recalled its controlled
LOG-PRIVATE-HOST-CEDAR marker. No real user data or graphs were used.

Implemented boundaries:

1. Native public routes require identity enforcement regardless of legacy off/log
   settings. Anonymous creation gets a server-issued subject; client user IDs are
   not authority. Existing login JWT and conversation-capability protocols remain.
2. Persist public ownership provenance on Conversation with attribute(...).
   Existing unbound conversations require authenticated owner proof; bind under
   the existing conversation lock, rotate old secrets and strictly flush before
   minting a capability or starting execution. Do not guess old token legitimacy.
3. Revalidate credential-bearing admissions after acquiring the conversation lock,
   so queued requests cannot outlive token expiry/revocation/secret rotation.
   Trust stays server-side; no model-supplied provenance or extra state database.
4. Session open must use identity and entry AccessControl admission without
   creating an Interaction/model run. Native refresh, uploads/voice and WebSocket
   capability gates must reject unbound ownership too.
5. Preserve valid anonymous creation/resume and authenticated media capabilities;
   document the explicit legacy-session reset/owner-proof migration. Trusted embed
   hosts continue to own their caller boundary; this is public HTTP/WS admission.

Acceptance: anonymous forged subject cannot attach to existing user memory;
anonymous open cannot mint for a victim; old weak capabilities fail before/after
owner migration; member bearer cannot resume owner sessions; credentialed create
ignores client subject overrides; anonymous tokens remain agent/session/channel
bound; ACL-denied opens and graph/binding/flush failures fail before provider or
external effects. Test real browser/model workflows on isolated graphs and record
all probe costs, state and durations. Do not claim bearer revocation, popup/member
or deployment coverage until exercised.

## Verified outcomes

The parent exploits persisted delivered results (283 and 510 provider-reported
tokens, USD 0.000632 and 0.001080 estimated respectively). The repaired native
routes reject victim session/open, old unbound capabilities, refresh and old
capabilities after owner migration with HTTP 401. Retired capabilities also fail
the voice ticket, TTS and upload gates. A migrated owner's valid capability still
mints a voice ticket. None of the rejected probes created an Interaction; session
open produced no empty-utterance Interaction or model run.

A normal member's create payload with the administrator's user ID produced a
bearer-owned Conversation under the member's actual login identity. Attempting
to open the administrator's session returned 401. An actual GPT-4.1 request
returned MEMBER-BOUND; after platform logout, the same bearer returned 401.
Native admission uses jvspatial's public `get_auth_service().validate_token`,
including locked revalidation, rather than another JWT/account service
(`jvagent/action/interact/session_token.py:482`, `:604`). The initial browser owner
positive found an incorrectly constructed unconfigured service; the final code
uses the server-owned service, and owner/member positives were rerun.

The real bundled jvchat displayed an MCP call and correct room/time response.
The bundled Messenger opened an anonymous session, executed that same read tool,
then survived iframe reload and recalled PUBLIC-CEDAR. Reopening with its
capability succeeded. The client now sends existing proof during session/open;
it does not retry authentication failure as a fresh anonymous create
(`jvmessenger/src/app/streaming/session.ts:226`). Actual usage and persisted native
delivery status are recorded for all four final successful runs.

Tests cover forged subject creation, legacy off/log settings, missing signing
configuration, unbound migration and strict durability with batching on/off,
agent/session/subject/channel/expiry capability scope, old-token retirement,
secret rotation and account revocation after preflight, graph/flush failures,
agent ownership and entry-policy rejection before binding or Interaction.
Frontend tests cover proof transmission and no silent reset after 401.

## Limits and remaining acceptance work

This qualifies ordinary-member browser API admission, not a member-facing
authenticated UI: jvchat is an admin reference client and Messenger currently uses
anonymous capabilities. Admin-list denial was observed as a browser fetch error
and is not counted as a qualified 403 outcome. Public popup tool rows did not
render despite SSE tool events; jvchat rendered them. Voice ticket admission was
tested, not STT/TTS provider audio or WebSocket sessions. No throughput claim,
distributed identity race qualification or legacy release migration follows from
these probes. Signed-in users still intentionally receive scoped bearer-owned
session capabilities for media; logout invalidates the bearer, while revoking a
conversation capability requires secret rotation. Automatic bearer-to-capability
revocation propagation is not claimed.

Historical anonymous sessions without authenticated owner proof require a new
session. No automatic reset, migration of guessed user IDs or promotion of old
capabilities is permitted. The transitional legacy execution paths and settings
remain until W5 migration/deletion. Recovery, deployment and full release gates
remain open. Timing totals in usage still report zero; interaction timestamps and
browser durations provide measurements here, not a repaired timing API.


## Validation record

Affected interaction/memory/ACL/response/embed tests: 519 passed, 2 skipped
(16.77 s). Native/PG suites: 211 passed each on Python 3.11 and 3.12; the added
identity suite: 22 passed on each. Messenger: 44 tests across 7 files passed;
TypeScript checking and both Vite builds passed. Actual successful final model
runs used 2,542 provider-reported tokens over six requests, USD 0.005684 estimated
from pricing metadata. Neither provider invoiced cost nor general performance
improvement is claimed. Graph interaction durations were 5.812 s (admin MCP),
3.749 s (member), 3.158 s (popup MCP), and 2.344 s (popup reload recall).

Tested source blobs are in the machine receipt; the final all-file hooks and
normal push/full-suite results are recorded when complete. The first expiry test
used a nonpositive TTL that the mint helper intentionally replaces with its
default; the final regression explicitly signs an expired claim. The entry-policy
regression uses a real denying AccessControlAction rather than an ignored
SimpleNamespace stand-in. These were test-fixture corrections, not relaxed runtime
assertions.
