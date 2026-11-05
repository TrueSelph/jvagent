# Native unknown-usage reserves — 2026-11-05

Parent `20fb17683338bd84ad8f62a96fc092d1ef8efcd0`. All nine parent CI checks passed.
This increment adds a bounded operator recovery operation, not general effect,
delivery, abandoned-worker or enterprise recovery qualification.

## Ownership and protocol

The [native usage reserve](../GLOSSARY.md#native-usage-reserve) is application policy.
It uses public SpendLimits.status and SpendEntry on the existing graph-backed
BatchSpendStore. No SDK key grammar, private SDK API, request executor, replay engine,
new database, provider price table or dependency was introduced. Execution and
operator key resolution share one budget declaration
(`jvagent/harness/spend_store.py:14`, `jvagent/harness/native.py:300`). Public interface:
[official spend documentation](https://pydantic.dev/docs/ai/harness/spend/).

An administrator inspects `/api/actions/{action_id}/native-recovery/{interaction_id}`
and posts to the same path plus `/usage-reserve` with this body shape:

```json
{
  "reserve": {
    "digest": "sha256:<digest from inspection>",
    "usd": "0.01",
    "evidence": "Reference to the operator's conservative allowance policy",
    "reason": "Interrupted request with unavailable provider billing"
  }
}
```

Both routes require platform admin authentication. The authenticated request principal
supplies audit identity; request fields cannot supply actor or replace the receipt.
Scope is resolved through the enabled native Action and Conversation's actual Agent,
user and session; distributed execution remains refused. Responses expose recovery
facts, not model/tool payloads (`jvagent/action/native_harness/endpoints.py:30`, `:71`, `:105`).
jvspatial materializes nested request models as dictionaries, so the handler explicitly
revalidates the declared request. Missing/invalid requests return validation errors;
no raw attribute exception or unvalidated scalar reaches accounting.

The amount is a positive, finite estimate with at most six decimal places, up to
USD 999999.999999. It is an additive reserve chosen under administrator policy,
not a guarantee that the eventual invoice will match it. Existing unknown token and
cost counts remain unchanged. A separate model_usage_reserve metric contributes
estimated dollars without inflating model requests or tokens
(`jvagent/memory/interaction.py:614`, `jvagent/harness/reconciliation.py:56`).

Approval binds execution identity, run/step facts and original billing by digest.
Late execution facts invalidate approval. Intent persists first, then both public
budget entries with a replay token, then the applied receipt and metric. Strict
acknowledgement failure leaves admission closed; retry cannot charge twice. The
existing failed-history path flushes accounting strictly before admitting another
request (`jvagent/harness/reconciliation.py:14`, `:37`, `:56`,
`jvagent/harness/recovery.py:117`). No receipt, checkpoint or effect is erased.

## Actual browser and graph evidence

Disposable app/PG graph on 8136, jvchat on 3136, real OpenAI GPT-4.1. The target
was the prior round's cancelled, no-effect request. Unchanged public SDK checkpoints
supply its accepted input. Runtime functions were not patched.

| Stage | Fresh evidence |
|---|---|
| Before approval | [Unknown charge retained; zero dollar counters](2026-11-05-native-reserve-before-smoke.json) |
| Admin approval | [USD 0.01 charged in both windows; one reserve marker](2026-11-05-native-reserve-applied-smoke.json) |
| Identical retry | [Counters/marker counts unchanged](2026-11-05-native-reserve-repeated-smoke.json) |
| Actual-model continuation | [Delivered result, original cancelled status and uncertainty retained](2026-11-05-native-reserve-final-smoke.json) |

[Browser receipt](2026-11-05-native-reserve-browser.json) records inspection, stale
approval rejection (422), successful approval/retry (200), real chat frames and
visible reply: “Yes, I can continue this conversation.” It reports one request,
1,258 provider-reported tokens, USD 0.002576 estimated, 2.429 seconds SDK duration
and 3.641 seconds Interaction wall time. Conversation counters total USD 0.012576:
the prior USD 0.01 reserve plus the new request. The old unknown count stays one;
the new response has no unknown-cost calls. Provider-invoiced dollars are unavailable.
There were no tool effects to replay. Observer records no legacy execution modules.

[Permission probes](2026-11-05-native-reserve-permissions.json) use real fixture
accounts: anonymous inspection 401, ordinary-user inspection and reserve 403,
admin missing body 422. Cross-origin error responses can be opaque to browser
JavaScript; a same-origin API browser independently verifies those status codes.
An initial nested-model/dictionary mismatch was caught and repaired before this
qualified cohort; its direct regression now passes.

## Validation and remaining gates

302 native/real-PG tests pass on each Python 3.11 and 3.12. New PG cases cover
immediate/deferred persistence of the receipt, retained uncertainty and both budget
debits. Tests cover stale facts, late changes, invalid amounts/scope, unresolved
effects, acknowledgement failure, immutable retry, shared-budget exhaustion and
authenticated actor binding. Eleven legacy-compatible endpoint tests pass, including
foreign-Agent scope and malformed request rejection. Final affected memory/endpoint
tests passed 210 with two skipped in 8.34 seconds. An earlier 207-test run took
266.77 seconds with a Redis lease pause, its instrumented rerun 7.05 seconds,
and isolated lease tests 11 passed in 1.81 seconds. This unexplained
timing variability remains a test limitation. Normal pre-push full suite and new-head
CI remain independent gates.

This operation only covers terminal failed/cancelled runs already eligible for the
existing accepted-input policy. Conversation mutations, unresolved effects, uncertain
delivery, running/abandoned ownership, unqualified historical checkpoints and changed
approved facts remain blocked. Earlier blocked-notice chains, provider invoices,
under/over-reserve correction/refunds and historical receipt migration need further
qualification. An operator must choose a conservative reserve; this does not discover
provider charges. No automatic reset or rollback is inferred. Broader compatibility,
migration, legacy deletion, performance comparison and release gates remain open.
