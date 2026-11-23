# Native repeated interview items — 2026-11-05

Parent `1dda0a743cd920098f85b134282a6ec2c5073dd4`; all nine parent CI checks passed.
Branch `feat/pydantic-harness-rebuild`; author `eldonm`. This qualifies the bounded
built-in repeated-item profile below, not all interview extensions or the release.

## Domain compatibility and ownership

Existing `for_each.fields` declarations and graph records remain the domain model.
Two explicit tools declare ordered IDs and validate answers/optional skips for one
ID. They run through the existing caller-scoped ConversationMutationTool and public
SDK effect receipts. The SDK still owns scheduling, model execution and persistence;
there is no new routing loop, provider wrapper or state database
(`jvagent/action/interview/native_repeats.py:55`, `:78`,
`jvagent/action/interview/native.py:151`).

Membership replacement retains matching completed records and pending answers for
their original ID. Removed IDs lose their answers. Child keys must be globally
unique within the spec; nested repeats, child branches/hooks, custom validators and
processors remain gated. Parents use text validation and one collection is active
at a time. Existing Python expansion hooks require migration to these domain tools
or separate effect qualification; arbitrary existing extensions are not implicitly
enabled (`native.py:20`, `native_repeats.py:78`).

Native generic `set_fields` refuses repeated parent/child keys and nonempty staged
payloads; ID-scoped writes validate the entire patch on a session copy before
saving. Unknown IDs, required skips, nested values and duplicate membership are
rejected. Future answers are validated before staging. Scalar JSON answers become
text for the existing validators; fractional numbers still fail a digits-only
field. Nonfinite floats are invalid (`native.py:224`, `native_repeats.py:38`, `:78`).
This closes the native staging bypass; it does not qualify the legacy staging API.

Completed records are no longer replayed through scratch staging. Completion upserts
by ID in declared order and skips already completed IDs, fixing duplicate/out-of-order
records after insertion (`for_each.py:311`, `:420`). Review fingerprints now bind
child records as well as top-level values. Both review and completion refuse pending
repeated collections. A missing historical fingerprint requires a fresh review,
instead of silently assuming unchanged data and then looping against completion
(`engine.py:1823`, `:1831`, `:1849`, `:2002`). Non-repeated fingerprints retain their
existing representation. Explicit status exposes records; an individual patch returns
only its updated/current item and next field, avoiding a full collection/catalog
response on every child write (`engine.py:2288`, `native_repeats.py:78`). This is a
response-size design choice, not a measured performance claim.

## Actual-provider browser evidence

Disposable app/graph: API 8136, jvchat 3136, OpenAI GPT-4.1. Personal deployments were
untouched. [Browser events and visible replies](2026-11-05-native-repeats-browser.json)
and fresh SQL exports include source blobs, request outcomes, durations and usage.
Exports retain the final typed SDK snapshot per Interaction and full record counts;
`sha256:` is a display prefix for snapshot hashes, not a changed persisted value.
No execution functions were patched.

The [initial attempt](2026-11-05-native-repeats-initial-smoke.json) exposed a real
model/schema mismatch: quantities arrived as JSON integers while the new adapter
required strings. Writes were rejected and the model retried, finally asking for
information already supplied. HTTP 200/delivered therefore did not establish a
successful workflow. That failed scenario consumed 27,965 reported tokens over
11 model requests, USD 0.028684 estimated, 23.846 seconds server duration.
It remains recorded; the fixture's incomplete task was not claimed completed.
The adapter now admits finite scalar answers and still runs domain validation.
SDK regressions use actual JSON integer arguments and reject nested/fractional input.

After the repair, a fresh conversation completed four real model-backed turns:

| Turn | State read back from graph | Server seconds | Reported tokens | Estimated USD |
|---|---|---:|---:|---:|
| Collect A1/C3 | Review, task active; quantities 1/3, notes skipped | 9.003 | 11,101 | 0.012560 |
| Insert B2; correct C3 | Ordered A1/B2/C3, quantities 1/2/4 | 7.744 | 14,494 | 0.011708 |
| Correct only B2 | Fresh review, same membership, quantities 1/5/4 | 6.352 | 13,858 | 0.009584 |
| Restart; confirm | Task completed, interview scratch removed | 4.512 | 10,338 | 0.006192 |

State exports: [collected](2026-11-05-native-repeats-collected-smoke.json),
[inserted](2026-11-05-native-repeats-inserted-smoke.json),
[reviewed before restart](2026-11-05-native-repeats-reviewed-smoke.json),
[completed after restart](2026-11-05-native-repeats-final-smoke.json).
Final SQL checks confirm four closed/emitted/delivered turns, completed SDK effects,
completed domain task and cleared session. Runtime source hashes match the working
tree; the restarted process has no legacy execution modules. UI shows tool activity,
reviews, final completion and request statistics. Browser durations differ slightly
from server durations and are retained separately. Successful cohort: 49,791 reported
tokens, USD 0.040044 estimated; full round including failure: 77,756 tokens,
USD 0.068728 estimated. Dollar costs are estimates, not provider-reported charges.

## Regression and qualification limits

Both integrity regressions failed against the parent: insertion produced duplicate
records and a child-only correction could complete against stale review. Tests now
cover those failures, native bypass attempts, invalid future answers, ID scope,
membership ordering, manual review, missing historical review fingerprints, SDK
receipts and fresh PostgreSQL persistence with deferred saves both on and off.
The existing interview suite passes 301 tests. Native/profile/host/real-PG cohorts
pass 323 tests on Python 3.11 (66.38s) and 3.12 (70.36s), 256 warnings each.
All-file hooks pass after staging and formatter reruns; final publication checks
remain mandatory.

No claim is made for arbitrary hooks, nested repeats, simultaneous active collections,
park/resume, historical state migration, interrupted-write reconciliation, alternate
providers or release performance. Broad subagent/external-effect/deployment gates,
configuration migration and legacy/pilot deletion remain part of the active goal.
