# Native browser authentication during outages — 2026-11-05

Parent `378201225561f686356ba1b2f3352dbfc1d6732e`; all nine parent CI checks
passed. Branch `feat/pydantic-harness-rebuild`, author `eldonm`. This qualifies
the recorded development-client profile, not the complete release.

## Confirmed defects and implementation

The parent cleared login/refresh credentials after network errors, rate limits,
server failures and malformed refresh responses. It also inferred authentication
failure from opaque network errors and could retry an authenticated write whose
outcome was unknown. Six initial regression cases failed before implementation
(`jvchat/src/config/api.auth-recovery.test.ts:1`).

Refresh now distinguishes explicit rejection of the current refresh credential
(401/403 on the refresh endpoint) from transient failures. It preserves credentials
for network, timeout, 429, 5xx and malformed responses. A shared promise replaces
the boolean/failed-callback queue, bound to the captured access credential, refresh
credential, caller and selected server. Late completions cannot replace or clear
another login. Proactive hook refresh uses the same service rather than duplicating
token writes (`jvchat/src/config/api.ts:51`, `:242`, `:520`,
`jvchat/src/hooks/useAuth.ts:57`). No new credential store or dependency was added.

Interaction submission now refreshes an expired login before constructing its
headers. It does not silently omit that bearer and continue as an anonymous/session
capability request. A cancelled interaction can stop waiting while a shared refresh
finishes for other callers. Refresh transport has a 15-second deadline. A late 401
for a different request credential cannot clear the new login
(`jvchat/src/config/api.ts:745`, `:764`, `:800`).

Application permission denial (403) is distinct from login expiry (401). Opaque
network failures do not trigger refresh or retry. A write may have committed before
a lost response or HTTP error, so host/path compatibility fallback for writes is
permitted only after explicit 404; this also holds if logout occurred while the
request was in flight. Existing 404 auth/interact route compatibility remains
covered (`jvchat/src/config/api.ts:158`, `:390`). Explicit pre-execution 401 retains
its existing bounded authentication recovery; this does not qualify arbitrary
external-effect retries.

This increment changes client transport/authentication services. jvspatial still
owns server authentication, while public Pydantic AI/Harness still owns model and
tool execution. No alternate harness loop, state database or provider adapter was
introduced. Runtime source across the two touched files has 51 fewer lines than
the parent; that is maintenance footprint evidence, not a performance claim.

## Actual browser/provider/outage evidence

Disposable backend 8136, Vite reference client 3136, PostgreSQL
`jvagent_native_host_context`. Browser context `native-auth-outage`, synthetic
administrator. Personal app deployments were not changed. The disposable auth
lifetime was shortened for actual JWT expiry; no execution function was patched.

1. [Baseline browser](2026-11-05-native-auth-baseline-browser.json) and
   [fresh SQL](2026-11-05-native-auth-first-baseline-smoke.json): actual GPT-4.1
   returned BEFORE, closed/emitted/delivered in 5.065s, 1,554 reported tokens.
   The first YAML lifetime override did not beat the disposable `.env`: browser
   inspection showed about 3,578 seconds remaining. This is retained as a setup
   mistake, not counted as an expiry test. The fixture env override was corrected.
2. A real public refresh request obtained a server-issued one-minute JWT in the
   existing browser storage. A concurrent successful proactive refresh rotated
   its access token during setup; the offline comparison baseline was captured
   after that completed. The recorded credential expired at 22:31:52 UTC.
   Neither JWTs nor refresh/session tokens are exported.
3. Backend PID 4101 was deliberately stopped with SIGKILL. With the JWT genuinely
   expired, [outage browser proof](2026-11-05-native-auth-outage-browser.json) shows
   the conversation stayed open, access/refresh/caller values were preserved,
   and send displayed a safe temporary sign-in-service failure. There was only
   the original baseline interaction POST; no outage interaction POST occurred.
   Background/proactive authentication retries did occur, with no model calls.
4. [Before outage](2026-11-05-native-auth-before-outage-smoke.json) and
   [during outage SQL](2026-11-05-native-auth-during-outage-smoke.json) have exactly
   unchanged Interactions and conversation spending. The latter explicitly labels
   its module-state file as the dead process's last observation, not a live handle.
   The final global query finds zero NATIVE-AUTH-OUTAGE Interactions, including
   outside the selected conversation; there was no anonymous substitute run.
5. After confirmed backend startup, a new user send refreshed the expired login
   and returned RESTORED. [Recovered browser](2026-11-05-native-auth-recovered-browser.json)
   and [SQL](2026-11-05-native-auth-recovered-smoke.json) show the same authenticated
   principal and session. The reply is closed/emitted/delivered, 4.992s,
   1,580 reported tokens. The new access credential differs; refresh credential
   and caller remain unchanged in this nonrotating-refresh fixture.
6. Final write-fallback hardening was followed by a new production build and page
   reload. [Final browser](2026-11-05-native-auth-final-browser.json) restores the
   selected history without duplicate replies and returns STABLE through a third
   actual GPT-4.1 call. [Final SQL](2026-11-05-native-auth-final-qualified-smoke.json)
   records 3.296s, 1,604 reported tokens for that turn, the same principal/session,
   and still zero outage Interactions.

Total: three actual model requests, 4,738 provider-reported tokens, USD 0.005138
estimated. Actual provider dollars were not supplied. The outage added zero model
usage/spend. Auth requests are not model requests and are retained separately in
the wire record. This is a bounded functional cohort, not an SLA/load benchmark.

Backend runtime source hashes match and no legacy execution modules were loaded.
The frontend is independently served: final evidence records current frontend
source blobs, while module_observation frontend hashes remain backend-startup
observations. [Served-bundle SHA256 proof](2026-11-05-native-auth-bundle-proof.json)
matches the actual main, useAuth and ChatInterface build files. It is not a freshly
packaged Python-wheel client qualification.

## Checks and remaining gates

Frontend: 159 tests in 14 files passed, including 19 new authentication/transport
cases; production build passed. The added cases cover transient preservation,
explicit rejection, fresh bearer before dispatch, shared refresh, late caller/
server responses, permission denial, cancellation, stalled refresh, stale 401,
unknown writes after logout/server error and explicit route compatibility.
Python static web-client server tests: eight passed. Native SDK execution code
was not changed; its parent has exact-head native/PG CI evidence. All-files hooks,
normal full pre-push and new-head remote CI are separate milestone gates.

Remaining: bounded graceful shutdown with active SSE readers; earlier scheduler
event-loop-unavailable errors; multi-tab/cross-tab refresh rotation and outage
load/Retry-After qualification; clock-skew and historical credential profiles;
per-message failure presentation (the unadmitted user row remains visible after
recovery, but its global error clears on the next send); other clients/channels and
packaged-wheel delivery. This does not qualify abandoned execution, uncertain
external delivery, data/config migration, legacy/pilot deletion or enterprise release.
