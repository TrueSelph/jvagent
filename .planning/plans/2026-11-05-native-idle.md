# Native idle delivery renewal — 2026-11-05

Parent `5acc988e0ad149acf8bbc15983cd814e9fb3d8df`: eight CI checks passed;
jvchat [failed ESLint prefer-const](https://github.com/TrueSelph/jvagent/actions/runs/42124650337/job/127585601340). The local frontend lint gate had been omitted.
The promise declaration is corrected, and npm lint is now part of this round's
validation alongside tests/build. Branch `feat/pydantic-harness-rebuild`, author
`eldonm`. This is a bounded client qualification, not release approval.

## Findings and implementation

The [parent idle observation](2026-11-05-native-idle-parent-expiry.json) shows an
original subscribed bearer expired, the reader received session_access_revoked,
and the client stopped instead of renewing with its valid refresh credential.
[Manual reopening](2026-11-05-native-idle-parent-reopen.json) refreshed the login
and received four frames with HTTP 200, but the old background error remained
visible. These are separate renewal and presentation defects.

The existing read-only subscription now renews within the captured caller/server
scope when an Unauthorized outcome accompanies a bearer inside the existing
browser expiry window. That window includes the 30-second lead used by
isTokenExpired, not only the server's exact expiry instant. It continues only
after obtaining a different, fresh credential. It can also reuse an already-renewed
credential. A fresh refusal outside that window, forbidden access, changed identity
or host, missing credential, and a revoked Mode B capability do not permit this
renewal path (`jvchat/src/config/api.ts:882`, `:913`,
`jvchat/src/utils/storage.ts:912`). Server authorization remains authoritative.

This loop reconnects only reply/subscribe. It never calls interact, a model, tool,
TaskMonitor, or channel adapter. Normal EOF/network failure still uses the existing
hook backoff. Renewal requires credential progress; a stale renewal cannot cause
an immediate reconnect loop. Abort and caller ownership remain binding.

Background errors are now separately scoped to caller/agent/conversation. A new
scope or healthy reader retires them, while a foreground failure keeps priority.
A 403 uses a permission-specific background message. This does not solve per-message
foreground failure presentation (`jvchat/src/hooks/useStreaming.ts:140`, `:272`,
`:285`, `:299`, `:1315`). No new dependency, store or harness execution engine
was added. The native SDK composition and graph adapters are unchanged.

## Actual browser, model and graph proof

Disposable app/graph 8136, Vite client 3136, PostgreSQL
`jvagent_native_host_context`; browser context native-idle-renewal. The fixture
issues real one-minute login JWTs. Credentials were obtained through normal login;
no JWT payload, clock, execution function or provider response was patched.

- READY was delivered through actual GPT-4.1. A synthetic future-dated TaskStore
  task was queued through the admin-only fixture, without execution. The captured
  original subscribed credential expired at 23:00:39 UTC.
- [Renewal browser](2026-11-05-native-idle-renewed-browser.json): the old reader
  ended with session_access_revoked, refresh succeeded, and a new reader opened
  at 23:00:42.419 UTC. The caller stayed unchanged, no background error appeared,
  and there was still exactly one interaction POST. Proactive and reader refresh
  requests are recorded separately; this is not a one-auth-request claim.
- [Before](2026-11-05-native-idle-before-renewal-smoke.json) and
  [after renewal SQL](2026-11-05-native-idle-after-renewal-smoke.json) have exactly
  unchanged Interactions, pending task and spending. Renewal did not bill a model.
- After further idle renewal, the fixture called real TaskMonitor.tick for the
  queued task. [Browser/tool/usage receipt](2026-11-05-native-idle-task-browser.json)
  shows the scheduled MCP lookup and Cedar/14:30 answer beside READY, without
  another user turn. The result owns its tool group and two-attempt usage badge.
  Dispatch HTTP 200 took 4.388s and the task completed.
- [Fresh SQL](2026-11-05-native-idle-task-delivered-smoke.json): both turns are
  closed/emitted/delivered; the SDK MCP effect and domain task result are recorded.
  Foreground duration 2.240s, scheduled duration 3.968s. Three provider requests,
  4,099 reported tokens, USD 0.008486 estimated. Actual provider dollars were not
  supplied. The browser made only its original interaction POST; scheduled SDK
  execution used the existing server task entrypoint.
- Negative check: the real public revoke-all endpoint returned HTTP 200 and
  revoked 18 synthetic-user tokens at 23:03:47.991 UTC. The
  [preserved request log](2026-11-05-native-idle-revocation-network.txt) records
  a subsequent refresh 401 and no additional interaction POST.
  [Browser state](2026-11-05-native-idle-revoked-browser.json) returned to login
  with access/refresh/caller credentials cleared. The navigation intentionally
  destroyed page-local wire capture; preserved DevTools requests supply this
  negative boundary's evidence. This does not claim all independently issued
  session capabilities were revoked by the login-token endpoint.
- [Post-revocation](2026-11-05-native-idle-after-revocation-smoke.json) and
  [final graph readback](2026-11-05-native-idle-qualified-smoke.json) preserve
  exactly the delivered Interactions, completed task and spending. No execution
  replay or extra model cost followed revocation.

Backend source hashes match and no legacy execution modules loaded. Frontend
source blobs are recorded separately from backend-startup observations. The
[served-bundle hashes](2026-11-05-native-idle-bundle-proof.json) match the actual
main/useAuth/ChatInterface files. This qualifies the source reference client,
not a freshly packaged Python wheel or a broad latency/load comparison.

## Checks and remaining gates

Frontend: 167 tests passed in 14 files, including expiry-frame/HTTP renewal,
fresh refusal, revoked Mode B, stale renewal, scoped errors and foreground-error
priority. npm lint and production build pass. Eight Python web-client server
tests pass. All-files hooks, normal full pre-push and exact-head remote CI remain
separate milestone gates. The parent CI lint failure is retained, not rewritten
as a green parent.

Production-only npm audit reports five high-severity package advisories and no
critical production advisories. [The dependency record](2026-11-05-native-ui-production-audit.json)
contains affected ranges, fix availability and primary advisory URLs; presence
does not prove exploitable exposure in this static client. Mitigation/exposure
review is an open release gate. No dependency was updated in this increment.
The [full build/development audit](2026-11-05-native-ui-full-audit.json) reports
23 entries, including critical Vitest/tinypool entries. Build/test supply-chain
exposure also needs assessment and qualified remediation; it is not dismissed
because those packages are outside the production-only audit. Registry fix
proposals are recorded rather than applied blindly.

Still open: bounded graceful shutdown with parked readers; scheduler loop
availability and automatic timer deployment (the fixture explicitly ticks the
domain scheduler); cross-tab/rotating refresh, outage/load/Retry-After and clock
skew; historical credential/capability migration; other channels/providers and
packaged-client delivery; uncertain external effects/delivery; final migration
and legacy/pilot engine deletion. The complete rebuild goal remains active.
