# SDK skill-load retry receipts and failed-run follow-up

## Confirmed defect and change

The Storefront cohort exposed four unfinished capability-load receipts. New
public-SDK regressions reproduce the cause: a duplicate `load_capability` request
raises retry control flow, which core 2.66 excludes from the ordinary error hook
after Harness 0.66 StepPersistence has recorded `started`. A run that subsequently
succeeds and one that exhausts retries both retain an unfinished receipt.
The parent implementation fails both regressions.

The existing ReadRetryReceipts adapter now includes this factory's official
declarative skill loader. For known retry rejections it invokes the public
StepPersistence error hook, which records the failed effect/event and re-raises
the same error for SDK retry handling. It adds no retry loop, checkpoint system,
database, replacement skill loader or dependency. Cancellation and storage
failures propagate; write effects are outside the receipt allowlist. Sources:
`jvagent/harness/native.py:394`, `jvagent/harness/read_retries.py:10`.
Remove this adapter when upstream terminalizes retry control flow through its
public lifecycle; retain the protocol and uncertainty regressions.

Actual browser exhaustion also exposed a misleading service-configuration
notice. A public UnexpectedModelBehavior directly caused by ModelRetry or
ToolRetryError now records `reason: retry_limit` and publishes a retry-limit
notice. No SDK exception-message parsing or private argument text is used.
An unrelated protocol cause retains the existing generic category. Sources:
`jvagent/action/native_harness/native_harness_action.py:671`,
`tests/harness/native/test_native_action.py:44`.

## Actual GPT-4.1 browser cohort

[Machine evidence](2026-11-06-native-skill-load-retries-smoke.json) binds the parent,
source/process epochs, SDK state, body observations, request outcomes/durations,
usage and raw-record comparison digests. [Browser observations](2026-11-06-native-skill-load-retries-browser.json)
retain visible tool activity, both notice versions, follow-ups and restored usage.
A fresh disposable PostgreSQL app/backend 8167 uses source jvchat 3140 and the
existing mock Storefront assets. Runtime functions are not patched.

1. An explicit retry-handling request makes the actual model load the FAQ skill
   twice and read the trial terms. The graph records completed load, failed
   duplicate and completed FAQ; the answer correctly gives the 14-day/no-card
   mock trial.
2. A request for three successive loads instead produces a parallel batch. It
   completes one and fails two, then explains those rejections. This is parallel
   duplicate coverage, not sequential exhaustion.
3. A separate-round request exhausts SDK retries. Both effects are failed and
   the run fails, but the initial UI misleadingly suggests a configuration issue.
   This pre-notice-fix result remains unchanged in history.
4. After the notice change and a cold restart, all prior interaction/conversation
   records are unchanged. An ordinary question in the failed session uses the
   existing accepted-input recovery frontier, calls FAQ/catalog and correctly
   returns Customer Insights at $249/month and onboarding timing.
5. The same separate-round adversarial request fails at the retry limit with
   the corrected visible notice and persisted reason; both effects are failed.
6. An ordinary same-session follow-up succeeds through accepted-input recovery
   and returns the actual trial and product facts. Reload restores failure and
   success history/tool counts/statistics without new records or spend.

All fifteen effects have exactly one matching terminal SDK event: eight completed
and seven failed, zero started. The six turns reconcile fourteen provider
requests, 19,207 provider-reported tokens and USD 0.028112 estimated from SDK
pricing, with zero unknown usage. These are not provider-billed dollar totals.
Conversation domain-task arrays remain empty. Both failed runs and their costs
remain persisted. These adversarial tests are not a representative latency or
accuracy benchmark; no optimization claim is made.

## Checks and boundaries

Twelve receipt cases cover duplicate, unknown and traversal-looking skill IDs,
normal correction and retry exhaustion, plus cancellation, write and persistence
failure exclusions. Host tests cover both typed retry causes, private-detail
redaction and an unrelated protocol cause. Sources:
`tests/harness/native/test_read_retries.py:19`,
`tests/harness/native/test_native_action.py:44`.
The host/receipt slice passes 46. Final native/host/real-PostgreSQL lanes pass
703 on each Python 3.11/3.12 with the configured Docker profile. Publication
gates remain separate.
Earlier 697-case lanes preceded the notice cases and are not final-head evidence.

The prior Storefront graph is re-read and compared: every interaction and
conversation is unchanged, including its four historical unfinished receipts.
No historical repair or inferred rollback is implemented. This closes receipt
creation for known loader retry failures and qualifies local read-only follow-up;
abandoned/cancelled work, uncertain writes, graph outages, distributed ownership,
broader error groups, remaining migration, legacy deletion and release gates
remain open. See the [qualification matrix](2026-11-04-harness-qualification-matrix.md).
