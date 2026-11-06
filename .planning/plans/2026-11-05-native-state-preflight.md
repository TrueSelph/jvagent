# Retained-state cutover preflight

This is the first state-inspection increment, not an implemented graph importer.
`harness-migrate --state-snapshot` can inspect a private operator-supplied JSON
snapshot alongside the declaration plan. It performs no graph access, mutation,
message replay or model request (`jvagent/cli/harness_migration_state.py:38`).
State blockers suppress candidate creation (`jvagent/cli/harness_migrate.py:510`).
Without this option, existing configuration-only behavior remains compatible.

## Findings that shape the importer

The legacy ledger is `Conversation.context['_cost_usd_total']`, and settlement
only writes it when a conversation dollar ceiling is enabled
(`jvagent/action/orchestrator/orchestrator_interact_action.py:5492`). Therefore a
missing ledger cannot prove no historical spending. Pruned Interaction receipts
also cannot establish lifetime costs. The native store starts absent counters at
zero (`jvagent/harness/spend_store.py:38`); importing old sessions without an
explicit baseline would incorrectly reset their budgets.

The native history admission path deliberately refuses old Interactions without
qualified native history (`jvagent/harness/recovery.py:64`). This increment keeps
that refusal. It does not synthesize a successful SDK run or promote historical
system/tool content into current authority. The eventual importer must use public
Pydantic message/storage/spend contracts while preserving original evidence.

## Inspection contract

Input: exactly `conversation` (one persisted Conversation row) and `interactions`
(its retained persisted Interaction rows), using jvspatial's existing
`{id, entity, context}` envelope. The report has a raw-source SHA-256 digest and
static blocker codes, not transcripts, credentials, prompts or tool payloads.
Bounds are 2 MB and 64 retained Interactions. Duplicate keys/nonfinite JSON,
malformed identities, duplicate row IDs and oversized inputs are refused.

Inspection separates observed model events, unverified events, verified retained
receipt subtotals and the distinct legacy ledger. It does not substitute current
pricing or relabel a historical aggregate as a provider invoice. Missing receipt
provenance, contradictory aggregates, unreported/invalid cost and zero estimates
block. Explicit typed provider-reported zero and an unsupported zero estimate are
not conflated. Only reviewed explicit cost-record source markers are understood;
other old formats require separate migration or operator reconciliation.

Foreign owner/session/conversation scope, count/frontier mismatches, unfinished
or malformed tasks, retained terminal-task/effect state, uncertain delivery,
nontext history, unqualified Actions, unknown context and mixed native records
require reconciliation. The preflight does not validate graph edges, timestamps,
authenticity of the exported rows, a live ownership chain or a consistent capture.
Even a report without blockers is `review_required`; `cutover_authorized` is
always false. It is not a credential or an authorization boundary.

The input remains a private operator file; the report never grants permission to
apply it. Capture consistency, lock ownership, lifetime/pruned accounting, typed
history import, idempotent writes and rollback are required before apply exists.
The CLI creates no migration database and adds no runtime execution path.
See [usage and limitations](../runbooks/native-harness-migration.md).

## Actual historical-state inspection

The earlier disposable real-model legacy benchmark's Conversation
`n.Conversation.4b3b2d9b6e084a3eab5cee4a` retains five Interactions. Their metrics
contain seven model events, all unverified by the explicit per-call receipt
contract. The old aggregate usage display is not discarded, but is not accepted
as per-call provenance. Its conversation ledger records USD 0.031304, with its
actual/estimated mix unverified. The report correctly keeps that ledger distinct
from the zero *verified* receipt subtotal and seven unverified model events.

State blockers include receipt/aggregate reconciliation, retained tool/host state,
unmapped context (`new_user`) and unqualified trace/attempt events. The real CLI
also retains its independent declaration blockers. It returns exit 2, and source
Conversation/Interaction rows compare unchanged before/after inspection. The raw
snapshot is retained outside the repository with mode 0600; only the redacted
[report/readback receipt](2026-11-05-native-state-preflight-real.json) is committed.
This is no proof that the historical conversation has been migrated.

## Browser continuation and verification

CLI/state planning changed; runtime execution modules did not. The existing
isolated disposable fixture `/private/tmp/jvagent-native-canonical-skills-smoke`
remained on backend 8143 and source UI 3136. A new actual GPT-4.1 browser request
re-read the seeded handbook through `pageindex__search` and returned the correct
21 November 2026 / Orchid / CEDAR-83 facts with the handbook citation.

The delivered Interaction `n.Interaction.31f42fffe6664db4970228d7` records two
provider requests, 8949 reported tokens and USD 0.005712 estimated; no unknown
cost requests. Persisted turn duration is 4.605437 s; browser HTTP duration is
4.7891 s; displayed model duration is 3.47 s. Conversation accounting advances
from 8 / 18347 / USD 0.027142 to 10 / 27296 / USD 0.032854, with no reserves and
empty TaskStore. Neither fixture is a user deployment. No legacy engine nodes or
execution modules are observed in the native process.

An initial keyboard action had no focused composer and opened the debug panel;
it submitted no request. After closing it and explicitly focusing the composer,
exactly one new request was submitted. This is retained as a UI-automation setup
mistake, not a successful extra model test.
See [browser text/resources](2026-11-05-native-state-preflight-browser.json) and
[persisted result/accounting](2026-11-05-native-state-preflight-smoke.json).
Evidence binds parent revision and exact source blobs; the containing milestone
commit identifies the final code/test tree. Runtime observations retain their
startup hashes because no restart or runtime change was needed.

CLI/scaffold suites pass 275 cases on each Python 3.11/3.12. Isolated suppression
of state blockers makes the candidate-refusal regression fail. Other tests cover
foreign scope, duplicate IDs/JSON keys, file/size bounds, malformed counters,
missing/contradictory receipts, zero estimates, nonterminal/terminal tasks,
uncertain delivery and redaction. Two additional malformed-discriminator cases
first reproduced raw TypeError failures; typed guards now return redacted
blockers for both. No live source was patched for the mutation.
All-files hooks must rerun clean after formatting; commit/full-push and exact-head
remote CI remain separate publication gates.

## Next required implementation

Bind an operator-reviewed source digest to a quiescent graph and server-established
ownership; preserve immutable historical facts; establish a reconciled lifetime
spend baseline; compile ordinary historical text into public typed messages;
implement idempotent graph application and rollback rehearsal. Active pilot/task,
write/effect, uncertain delivery and mixed histories require their own qualified
migration. Only then can native admission consume the migrated history and the
old execution machinery be removed. The original full goal remains active.
