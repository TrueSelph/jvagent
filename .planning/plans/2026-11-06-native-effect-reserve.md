# Verified local writes with unknown billing — 2026-11-06

This advances W3/W4 of the [execution plan](2026-11-04-harness-rebuild-execution.md).
It extends [known-usage local write recovery](2026-11-06-native-effect-recovery.md)
through the existing budget reserve, with separate effect approval. It does not
qualify generic effects or complete the rebuild.
[Source/SQL evidence](2026-11-06-native-effect-reserve-smoke.json) and
[browser readback](2026-11-06-native-effect-reserve-browser.json) retain failures,
source epochs, outcomes, duration and usage. Parent 2067d7ce has all nine CI checks
green; publication and new-head CI are separate gates.

## Independent administrative decisions

Read-only SDK usage projection now exposes an unreported request without editing
the source. Effect inspection can report this uncertainty, but effect approval
and continuation require an applied reserve. Paying a reserve alone cannot permit
continuation. Both decisions remain authenticated administrator operations under
the conversation lock. Sources: `jvagent/harness/accounting.py:241`,
`jvagent/harness/effect_recovery.py:192`,
`jvagent/harness/effect_recovery.py:241`.

The existing POST `/api/actions/{action_id}/native-recovery/{interaction_id}/usage-reserve`
accepts the named `reserve` envelope. For one domain-verified local mutation it
requires a stopped, scoped owner, independently verified resource and matching
inspection digest. The explicit POST journals missing observations derived from
SDK facts, strictly flushes them, then debits the public SpendEntry budgets before
acknowledging. It does not change SDK checkpoints/effects or original run status.
The optional approval_digest preserves the pre-projection inspected digest;
digest binds the normalized source. Identical retries with either retained
inspection digest cannot charge twice. Sources:
`jvagent/harness/reconciliation.py:59`,
`jvagent/harness/reconciliation.py:100`.

After reserving, reinspect the effect and submit its new digest to the existing
`effect-approval` endpoint. Unknown billing remains reported and the reserve is
explicitly estimated. Approved continuation uses the same SDK history/deferred
results and initial read-only policy as the previous increment. An existing
known-usage approval from the retained 8151 fixture still validates against the
new source without rewriting it. Source: `jvagent/harness/effect_recovery.py:235`.

## Defects exposed by actual browser work

A budget already exhausted by a reserve was refused before provider dispatch,
but StepPersistence had already journaled its request-start boundary. This
produced a false unknown-cost attempt. Moving admission earlier removed that
marker but also lost the SDK's accepted-input checkpoint; a subsequent real
browser request exposed that abandoned implementation. Its old records remain
blocked rather than being retroactively fabricated.

The final composition preserves SDK checkpoint order. A public Hooks capability
calls the existing SpendLimits.before_model_request; a refusal is strictly
recorded in existing host telemetry, bound to SDK run, step and exact request
start timestamp. Accounting excludes only that observed pre-provider refusal
from attempted/billed usage. SDK events remain unchanged. The SDK retains its
normal inner checks and accrual; the accepted path incurs another local budget
read, not a second policy or request loop. Remove this adapter when upstream
persists admission-refusal facts itself. Sources:
`jvagent/harness/native.py:373`, `jvagent/harness/accounting.py:56`,
`jvagent/harness/accounting.py:303`.

A later live limit update also left execution using a warm cached Action list.
The generic update endpoint saved/reloaded the entity but did not invalidate
that list. It now invalidates the owning agent's Action cache after save and
before reload, including when reload fails. This changes an existing service
boundary rather than adding harness configuration machinery. Source:
`jvagent/action/endpoints.py:228`.

Both protective tests fail against isolated published parent 2067d7ce: preflight
billing remains falsely unknown, and a cached policy entity remains stale.
Current tests also bind refusal facts to their run/step/time/origin and verify
accepted-input recovery, stale file/owner/facts refusal, journal-flush refusal,
original-digest acknowledgement retry without double debit and cache invalidation
on reload error. Sources: `tests/harness/native/test_spend_event_boundary.py:124`,
`tests/harness/native/test_effect_recovery.py:321`,
`tests/action/test_action_endpoints.py:39`. Mutation logs are
`/private/tmp/jv-budget-preflight-mutation-final.txt` and
`/private/tmp/jv-policy-cache-mutation.txt`.

## Actual GPT-4.1 browser and PostgreSQL evidence

Disposable backend 8152, source-built jvchat 3140 and isolated PostgreSQL graph.
Execution functions are not patched. The monitor verifies its owned process,
physical write and completed effect, then SIGKILLs after a second SDK request
start without a completion receipt. A start alone does not prove provider send
or billing. Original SDK records and file inode/mtime/content remain exact.

| Final conversation scenario | Outcome | Interaction seconds |
|---|---|---:|
| Physical write, second request start, abrupt kill | Running original; completed write effect; unknown second request | Unmeasured |
| Follow-up before reserve | Recovery notice; no SDK/model work; IRIS-74 retained | 0.072091 |
| Follow-up after reserve, before effect approval | Still blocked; no SDK/model work; DAISY-74 retained | 0.071887 |
| Both approvals, backend restart, explicit continuation | Delivered, both codes recalled; no new effect | 3.465780 |
| Initial live-budget probe before cache repair | Incorrectly contacted model; paid receipt retained | 1.854662 |
| Ordinary file write before cache repair | Delivered; actual call/charge retained | 3.452982 |
| Live limit update after cache repair | Budget refusal; zero attempts/unknowns; two SDK snapshots retained | 1.334871 |
| Budget restored, subsequent write | Delivered policy-next.txt; accepted-input continuation | 3.555043 |

Admin reserve retries return equal receipt fields; JSON field order is not used
as equality. Before reserving, effect approval returns 422. After reserve,
the old effect digest also returns 422; reinspection permits approval. Both
approvals persist through restart. Member reserve/effect-approval calls return
403 and anonymous inspection returns 401. Browser CORS hides those error bodies;
server access logs establish status.

The initial cohort retains its false unknown, failed early-hook continuation and
blocked follow-up. A USD 0.000001 explicit conservative reserve covers that old
ambiguous record; it is not a zero-cost correction or invoice. The subsequent
fresh cohort qualifies the final path. One operator fetch during startup failed
before a request and was manually retried; no hidden model retry occurred.

Across all 15 Interactions, nine reported model receipts contain 6978 tokens and
USD 0.015528 from SDK pricing estimates. Three explicit reserves total USD
1.000001; total estimated spending is USD 1.015529. Fresh conversation counters
match requests, tokens and dollars exactly. Three unknown-cost events remain,
including the retained pre-fix false marker. No invoice or final settlement is
inferred. Deleting only the final session's local transcript and selecting it
after reload changes none of the 15 Interactions, ten SDK registrations, tasks or
spending. Completed replies/tool groups/usage restore; failed/blocked history is
outside that projection.

## Qualification and remaining work

Final native/host/real-PostgreSQL cohorts pass 492 on Python 3.11 (103.81s) and
3.12 (103.76s). Focused recovery/reserve/spend boundaries pass 24; final cache and
spend-boundary tests pass eight (1.17s); affected transitional accounting tests
pass 20 with three skips (2.34s). Counts overlap. Staged all-files hooks pass
without changes after formatting repairs. Normal full pre-push and exact-head CI
remain publication gates.

This closes the single stopped local write plus bounded uncertainty-reserve
combination. It does not settle provider invoices, retroactively repair abandoned
early-hook snapshots, qualify other/multiple/remote/child/background effects,
provide distributed fencing or protect hostile OS file races. Accepted budget
reads have not been benchmarked. Live multi-worker configuration, broader error
UX/history restoration, provider/deployment matrices, migration/rollback, legacy
removal, measured optimization and enterprise release gates remain open. No
second execution engine, state database or dependency was introduced.
