# Native WhatsApp tools and channel authority

Status: bounded local text profile qualified; reference cutover and release incomplete.
Parent revision: `7de4e90a`. The [machine record](2026-11-06-native-whatsapp-tools-smoke.json)
preserves runtime source hashes and each observed worker epoch. The
[browser record](2026-11-06-native-whatsapp-tools-browser.json) contains actual
provider-observer and reloaded jvchat snapshots. Earlier source epochs are
identified separately rather than presented as tests of the final source.

## Implementation

Four existing tool names and schemas retain their Action implementations through
native declarations (`jvagent/action/whatsapp/native_tools.py:39`). Named host
permission and verified live sender proof are independent admission requirements.
Official SDK dispatch, effects and graph-backed records remain the execution
foundation. No dependency, persisted entity field, database or retry loop is added.

Template/Flow sends require one printable bounded provider ID
(`jvagent/action/whatsapp/utils/message_receipts.py:6`). This is acceptance, not
delivery. A missing receipt retains the SDK mutation prefix and raises the typed
uncertain-outcome notice. Keys bind the Interaction, Action, sender and normalized
request. Encoder-ignored Flow options cannot bypass duplicate detection
(`jvagent/action/whatsapp/native_tools.py:60`). The StepStore refuses another call
claiming the key before dispatch (`jvagent/harness/step_store.py:144`). This is
single-writer protection, not provider-wide exactly-once delivery.

Public native HTTP replies remain local, and incoming request data is excluded
from native adapter metadata (`jvagent/action/reply/reply_action.py:382`). This
closes the raw-message escape hatch. Native external replies require matching
host facts in SDK registration. Nonexecuting notices can use live matching
adapter proof for immediate publication; replay cannot reconstruct that proof
(`jvagent/action/whatsapp/whatsapp_adapter.py:94`). Unbound notices fail closed
without importing the optional SDK. See [ADR-0058](../adr/0058-native-channel-authority-and-send-effects.md).

The three example skills use canonical hyphenated names. Service forms use SDK
discovery/loading instead of always-active pinning. The SOP treats submitted JSON
as an unverified receipt, without claiming signup, authentication or booking
completion. Persisted task/configuration owners are not automatically renamed.
The Action README retains operational provider/deployment configuration while
removing generic checklists that did not prove native qualification.

## Browser and model evidence

A disposable PostgreSQL app on 8173, jvchat on 3140 and an owned provider observer
on 8174 exercised actual OpenAI GPT-4.1. Nothing was sent to Meta or a person.
Signed Meta-shaped webhooks were submitted from the browser; their results and
SDK events were read from graph storage. This is not a customer WhatsApp UI test.
Forwarded HTTPS on trusted loopback simulates termination; actual TLS is untested.

Seventeen closed Interactions include thirteen model turns, two blocked follow-ups
with zero model requests, and two retained fixture admission failures. Initially
the fixture selected both a primary skill and catalog; native admission correctly
refused it. Explicit source synchronization corrected the owned fixture while
preserving failed records. A provider-observer classification mistake was also
corrected: physical sends are counted from nested `body.message.type`, not its
earlier derived flag. Original observations remain available in the private fixture.

Positive cases cover ordinary arithmetic, filtered catalogs, signup template,
signin/signup form, appointment form, permission restoration and restart. Negative
cases cover forged public channel/sender/raw-send metadata, revoked named grants,
untrusted form submissions and missing acknowledgements. Two genuine overlapping
requests keep denied and allowed senders separate. A duplicate two-call model
request sends exactly one Flow; the second call fails before another provider
send. Missing-receipt follow-ups remain blocked after restart with zero model
calls and no additional domain send. Fresh requests after a known pre-I/O
permission denial work once permission is restored.

Eight physical template/Flow sends comprise seven acknowledged acceptances and
one missing acknowledgement. Twenty-eight model requests reconcile exactly to
45,099 provider-reported tokens and USD 0.064986 **estimated** cost in conversation
spend journals. Actual provider dollar charges are unavailable; billing is not
reconciled. Each SDK run has one terminal event. Conversation tasks remain empty.
Reload leaves all seventeen Interaction rows and conversation records unchanged;
jvchat shows the persisted reply and 260-token/one-attempt statistics. Cold restart
checks preserve their previous snapshots. A later fresh request after permission
restoration updates only the prior denied row's usage timestamp; it is not claimed
as byte-identical historical state across that recovery operation.

## Regressions and checks

Full native/host/real-PostgreSQL lanes passed 788 cases each on Python 3.11 and
3.12 before the final optional-import guard and two batch-unknown additions.
Final-source affected slices pass 297 cases on each version, including WhatsApp,
native tools/egress/effect identity, native Action and reply metadata. Migration/
scaffold coverage passed 246; the full transitional suite also exited successfully
before the last small changes. Publication still requires clean all-file hooks
and the normal full-suite push gate.

Adversarial SDK cases cover permission/proof/selector denial, bounded malformed
receipts, timeout/cancellation, batch and repeated calls, first-unknown stopping a
later request, and retention of started effects on cancellation. Mutation checks
fail when semantic-key enforcement, native metadata stripping or native egress
authority is removed. Both-selector allowlist and encoder-ignored-option tests
were observed failing before their fixes. No mutated source remains.

## Open gates

The reference offline assessment now has [six blockers](2026-11-06-native-whatsapp-reference-blockers.json)
and ten format-admitted skill selections. This is not runtime migration approval.
Actual Meta/jvconnect, voice proof, serverless delivery, distributed ownership,
provider alias equivalence, uncertain-send operator reconciliation and durable
delivery replay remain unqualified. Broader compatibility, stored-state migration,
single-path cutover, legacy deletion, performance comparisons and enterprise
release gates remain open. No overall optimization or enterprise-readiness claim.
