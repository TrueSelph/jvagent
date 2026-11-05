# Native interview declarative branches — 2026-11-05

Parent revision `de9c4fd1f229369688d75c9520e7276c8d4228a0`; final runtime blobs
are recorded in the linked SQL receipts. This increment qualifies declarative
acyclic paths, not arbitrary hooks, repeated fields or the enterprise release.

## Implementation and simplification

Native admission validates unique field names, known operators, JSON-safe finite
conditions, membership operands, existing targets and acyclic paths before exposing
tools (`jvagent/action/interview/native.py:59`). Existing field resolution/pruning
remains unchanged (`jvagent/action/interview/flow.py:325`, `:465`). There is no new
model loop, router, provider adapter or state store. Official Harness executes the
same reviewed domain tools; the native SOP explains corrections and pruned answers
(`jvagent/action/interview/native_skill/SKILL.md:1`).

An adversarial correction exposed a stale-approval defect: manual completion could
accept revised values against a previous review. Completion now requires the saved
review fingerprint to match the current collected values; a missing historical
fingerprint also requires a fresh review (`jvagent/action/interview/engine.py:1998`).
The existing later-interaction confirmation check remains. Historical manual review
sessions need another review when they have no snapshot. Automatic confirmation
retains its explicitly configured semantics.

Removed four regex patterns, three helper functions and the under-extraction
steering path from the shared domain engine. Explicit submitted values, validation
and declared field handling remain; extraction belongs to the model and SOP
(`jvagent/action/interview/engine.py:539`). Legacy prompt parameter injection and
other unqualified responsibilities still exist; this is a narrow deletion.

## Real browser and fresh graph evidence

Disposable app/graph `jvagent_native_host_context`, backend 8136, jvchat 3136,
actual OpenAI GPT-4.1. No execution functions patched. Response-clone observation
captures tool events and drops credentials/capabilities. SQL uses a fresh database
client. Personal app/graphs were untouched.

| Turn | Outcome | Backend duration | Reported tokens | Estimated USD |
|---|---|---:|---:|---:|
| Premium intake | Start/store/review; task active | 9.551 s | 6,237 | 0.008808 |
| Correct to standard | Premium note pruned; email retained; new review | 5.278 s | 6,542 | 0.005038 |
| Confirm after backend restart | Task completed; live interview cleared | 4.659 s | 5,057 | 0.005356 |

All three requests delivered: nine provider calls, 17,836 provider-reported tokens,
USD 0.019202 estimated, zero unknown-cost calls. Provider-invoiced dollars were not
returned. Generic model-attempt/duration counters remain zero; use request timings,
not those counters, for this measurement. This small cohort establishes functionality,
not a general reliability rate or performance improvement.

[Browser transcript/events/timings](2026-11-05-native-branches-browser.json),
[premium review](2026-11-05-native-branches-premium-smoke.json),
[persisted branch correction](2026-11-05-native-branches-pivot-smoke.json),
[completion across restart](2026-11-05-native-branches-completed-smoke.json).
Both backend processes recorded the same runtime blobs and no legacy execution
modules. jvchat visibly displayed start/store/review/complete badges and token counts.

## Validation and remaining gates

299 affected interview tests pass. Native plus real-PostgreSQL cohorts pass 285 tests
on each Python 3.11 and 3.12. New tests cover invalid declarations, branch correction,
fresh SQL pruning/shared-field/task state, and review binding. PG cases run immediate
and deferred saves. Removing the review gate in an isolated test process causes
both stale/missing-snapshot regressions to fail; production source was not patched.
All-file pre-commit passed after correcting one variable-name type conflict.

Custom hooks/validators/processors/tools, for-each, scheduled/external callbacks,
delegated writes, operator reconciliation, configuration/state migration, deployment
and provider coverage, legacy-runtime deletion and release acceptance remain open.
All nine parent-revision CI checks passed; new-head publication checks are separate.
