# Native artifact downloads qualification

## Result and scope

This increment qualifies a bounded local file-publication service used through
the official SDK tool/effect path. It does not add an execution loop, database,
renderer or dependency. The [runbook](../runbooks/native-artifact-downloads.md)
defines the profile and settings. Source parent is `4993e3be`; the exact tested
runtime Git blobs, observer process epochs and effective package versions are in
the [persisted smoke record](2026-11-06-native-artifact-downloads-smoke.json).
All runtime source changes were present for the final actual-provider turn.

## Acceptance evidence

An isolated disposable PostgreSQL graph, 64 MiB local filesystem, backend 8157
and source-built jvchat 3140 exercised actual `openai:gpt-4.1`. The SDK script
container shares only the caller's existing user leaf. Export snapshots are
outside that mount. The record binds source blobs, identity, session, workspace,
effects, SDK snapshots, model receipts, storage objects and HTTP results.

- Text publication returns an immutable snapshot. Editing the original through
  SDK bash changes the original but not the already-issued download's hash.
- An SDK-produced PNG downloads anonymously as 69 bytes with SHA-256
  `b1ff9c8ea3a780bad09b346c423d2d0e46815926879b18e841d928376a946640`.
  Browser image decoding verifies one red pixel. The final tightened server-context
  guard also passes an actual model publication and anonymous browser fetch.
- A 45-second capability returns 200, then 404 after expiry. Invalid codes and
  wrong-Action URLs return 404. An earlier 15-second link was already expired
  at its first fetch and is not transition evidence.
- A live, correctly scoped named-permission denial stops publication with no
  extra proxy or export. Restoring policy permits publication without restart.
- jvchat displays the actual share tool, link, timezone-qualified expiry and usage.
  Backend restart restores prior history. Final reload restores ten Interactions
  and ten SDK run registrations without changing persisted rows or spending.
- Operational logs mask credentials while canonical SDK results retain recipient
  links. The two logging regressions failed before the fix. The final process
  log contains none of the issued proxy codes.

The [browser observations](2026-11-06-native-artifact-downloads-browser.json) retain
the earlier workflows; [final browser observations](2026-11-06-native-artifact-downloads-final-browser.json)
capture the final strict-admission turn and restored transcript. Published
evidence masks capability codes. Raw credentials and private `.env` stay outside
the repository.

Ten actual-model turns comprise nine delivered results and one expected denied
run. One delivered result failed the exact-content request by adding a newline;
one earlier malformed admin-denial probe omitted the caller ID and is not denial
proof. Both remain in the evidence and cost totals. Seven positive workflows
remain after those exclusions. The cohort used 23 provider requests and 61,935
provider-reported tokens. USD 0.074862 is estimated cost, not provider billing;
there are no unknown-cost calls in this cohort. Per-turn durations and request
outcomes are recorded, without claiming an optimization benchmark.

## Regression and publication gates

The native/host/real-PostgreSQL suite passes 558 cases on each Python 3.11
(116.95 seconds) and Python 3.12 (119.28 seconds). Staged all-files hooks pass;
push/remote checks are separate gates.
The affected legacy/logging/file-interface slice previously passed 222 cases;
the final admission/logging slice passes 33. Share regressions cover immutable
snapshots, invalid expiry, host admission, unsafe paths, byte limits, service
absence, tampering, scope, SDK authorization and uncertain publication failures.
Source: `tests/harness/native/test_file_share_adapter.py:1`,
`tests/logging/test_download_capabilities.py:1`.

Issuance uses the existing public App file/proxy services and strict deferred
flush. SDK mutation intent precedes publication; post-publication failure leaves
a failed non-retryable effect rather than permission to replay. Download returns
the verified bytes without another filesystem read. Sources:
`jvagent/action/file_interface/sharing.py:39`, `:119`,
`jvagent/action/file_interface/file_interface_action.py:35`, `:232`.

## Open gates

Expired snapshot cleanup, aggregate capacity, individual revocation UX, operator
reconciliation after interrupted issuance, remote/S3 storage, renderer libraries,
other channels and distributed deployment remain unqualified. Proxy access counts
are attempts, not acknowledged deliveries. Naive dependency expiry storage needs
cross-host/timezone qualification. The running server middleware replaces the
endpoint's sandbox CSP with `default-src 'self'; frame-ancestors 'none'`; effective
attachment, octet-stream, no-store and nosniff headers were verified over HTTP.
One navigation before backend readiness required signing in again; outage UX is
not qualified. Migration, legacy deletion and enterprise release remain open.
