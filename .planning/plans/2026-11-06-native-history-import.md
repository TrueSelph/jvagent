# Published-text history import — 2026-11-06

This advances W5 historical migration without closing full migration, deletion or
enterprise qualification. Parent revision: `afa0427deda32075d752ee670073f002a7e6c484`;
all nine parent remote checks passed. Exact exercised source blobs are in the
[SQL/runtime evidence](2026-11-06-native-history-import-smoke.json).

## Behavior and ownership

An administrator can approve a frozen projection of completed ordinary historical
conversation text and an explicitly estimated historical spend baseline. It uses
public SDK message types and official SpendLimits/SpendEntry APIs. Original
Interactions, prompts, tool records and legacy ledgers remain unchanged; no old
tool or model execution is replayed and no synthetic provider receipt is created.
Implementation: `jvagent/harness/history_import.py:69`, `:387`, `:516`.

The host adapter qualifies default-channel state with at most 64 legacy turns,
up to 32 already-qualified nonexecuting notices and a 2 MB serialized source
bound. Raw database rows are loaded before the serialized-size check; arbitrary
huge individual database records are not a proven memory-bound workload. Tasks,
unreviewed context, mixed native state, unsettled delivery and domain/effect tools
are refused. Historical tool traces are restricted to reply/use_skill. These are
explicit migration restrictions, not a new execution engine. Source:
`jvagent/harness/history_import.py:69`.

The protected Conversation receipt binds caller/session/source digest, canonical
user/assistant messages and operator approval. Native recovery validates that
projection and its spend marker before supplying it as SDK history. It rejects
instruction/provider metadata injection even if the message hash was updated.
Source: `jvagent/memory/conversation.py:119`, `jvagent/harness/history_import.py:333`,
`:516`, `jvagent/harness/recovery.py:27`. Stale admission objects reload the receipt
alongside counters (`jvagent/memory/conversation.py:368`). Model arguments cannot
write the authority key (`jvagent/harness/contracts.py:46`).

Three action-scoped administrative endpoints inspect, apply and roll back. POST
identity comes from the authenticated request, never an actor supplied in JSON.
The host verifies Action/Agent/Conversation scope and rechecks under the local
conversation lock. Source: `jvagent/action/native_harness/endpoints.py:29`, `:167`,
`:181`. This increment does not qualify distributed migration ownership.

Prepared intent is flushed before one idempotent SDK conversation spend entry;
an identical prepared retry verifies the source before finishing. Applied retries
verify the baseline without charging again. USD must cover the retained ledger;
tokens/requests must cover retained evidence. All historical requests remain
unpriced and the approved dollar amount stays estimated. No current-price
reinterpretation of historical provider invoices occurs. Source:
`jvagent/harness/history_import.py:372`, `:387`.

Rollback restores the initially empty native counter only while the original
frontier and complete counter digest remain unchanged. It retains a rolled-back
audit receipt, and identical rollback retries succeed. It cannot undo new native
execution, reapply a rolled-back approval, repair changed prepared sources or
restore an old released artifact. Source: `jvagent/harness/history_import.py:576`.

## Actual browser and graph rehearsal

The disposable PostgreSQL database copied 30 nodes, 29 edges and one auth user
from the earlier actual-model legacy benchmark. Refresh tokens were omitted and
the JWT secret rotated. Source app/graph remained untouched. Only the clone was
bootstrapped with NativeHarnessAction/ReplyAction; it had no remaining old
Orchestrator/model-service nodes. See clone/runtime facts in the
[evidence](2026-11-06-native-history-import-smoke.json).

Backend 8144 and jvchat 3136/page 40 used the isolated browser context. The cold
client required an explicit local conversation-index entry; no transcript was
written into browser storage. Server discovery and old transcript UI backfill
remain open. The saved fixture account was selected through the login UI after
an expired/invalid bearer returned 401 on the restart probe. This is not an
automatic credential-refresh qualification. See [browser evidence](2026-11-06-native-history-import-browser.json).

The first inspection refused timezone-naive historical timestamps. The operator
then explicitly approved interpreting them as UTC. The API does not infer their
original zone; that decision remains the operator's responsibility. Missing UTC
approval, stale digest, boolean tokens, forged actor and an underfunded baseline
were refused with 422. An ordinary member and anonymous caller were denied
403/401 in backend access logs. Their cross-origin fetches could not read those
statuses because jvspatial auth denials lack CORS headers. See
[API evidence](2026-11-06-native-history-import-api.json).

For the first conversation, five retained turns contain seven model events,
15,133 retained tokens and a historical ledger of USD 0.031304. The approved
baseline was USD 0.05 estimated, 15,133 tokens and seven requests. Duplicate apply
succeeded without another debit. After a normal backend restart, the same
approval still returned the original applied receipt.

The browser asked, without the answer in its prompt:
"What project code did I ask you to remember earlier? Reply with the code only."
Actual GPT-4.1 returned **CEDAR-47**. UI statistics showed one attempt, 480 tokens
and 1.9 seconds. SQL measured 2.3343 seconds for the Interaction and 1.915987
seconds at the SDK model-request boundary; browser end-to-end timing was not
measured. The new request has 474 provider-reported input tokens, six output
tokens and USD 0.000996 estimated from SDK pricing. No tool call was needed.

Fresh PostgreSQL readback proves all 16 original Interaction rows are identical
to the source, the new run is delivered, there is exactly one new registration,
and each typed SDK snapshot begins with the exact ten approved historical
messages. Native history_source is imported_published_text. Conversation totals
are USD 0.050996, 15,613 tokens and eight requests, with seven historical unpriced
requests. The separate legacy ledger remains USD 0.031304. Runtime observations
report no legacy execution modules loaded. See the [SQL evidence](2026-11-06-native-history-import-smoke.json).

A second five-turn conversation was applied twice and rolled back twice before
execution; each request returned 200 and final native spending is empty. Its
legacy USD 0.035402 ledger and original records remain unchanged. Rollback of the
first conversation after new execution returned 422 and retained its spending.

After the lease fix below, a second ordinary backend restart and actual browser
follow-up again return CEDAR-47 through the prior delivered SDK snapshot. Its
503 reported tokens cost USD 0.001042 estimated, with a 2.047641-second SDK
request and 3.025319-second Interaction. Both browser attempts succeed: two
requests, 983 reported tokens and USD 0.002038 estimated. Final conversation
totals including the separately estimated baseline are USD 0.052038, 16,116
tokens and nine requests; seven historical requests remain unpriced. All 16 old
rows remain unchanged. See [final browser evidence](2026-11-06-native-history-import-final-browser.json)
and [fresh SQL/source observations](2026-11-06-native-history-import-smoke.json).

## Validation and remaining gates

The focused import suite passes 34 cases. Native/host/real-PostgreSQL suites pass
443 cases on Python 3.11 (74.28 s) and 3.12 (90.82 s), with 294 warnings each,
after the lease fix and final browser restart.
Tests cover protected state, foreign identity/session, unsupported effects,
prepared retry after a spend write, hash/metadata tampering, counters, rollback,
stale cached admission and explicit naive-UTC policy. Source:
`tests/harness/native/test_history_import.py:1`.

The additional memory suite exposed a lease-cleanup stall around Redis heartbeat
tests. An independent deterministic cancellation-at-renewal regression fails on
the old implementation: Python 3.11 wait_for can return its child's successful
result while losing concurrent host cancellation. Renewal now uses
asyncio.timeout in the heartbeat task, preserving the deadline and cancellation
without a child wait_for race. Source:
`jvagent/memory/distributed_conversation_lock.py:133`,
`tests/memory/test_lease_renewal.py:65`. The new regression fails before the fix;
afterward the uninterrupted memory suite passes 200 cases with two real-Redis
infrastructure skips, and all 12 lease tests pass on Python 3.12. This does not
qualify real distributed migration or uncooperative/blocking renewal drivers.

The original memory process was deliberately interrupted for diagnosis and then
returned zero; that signal-assisted result is excluded. A first diagnostic script
cancelled itself on every renewal, including cleanup, and was terminated; the
corrected one-shot reproducer recorded terminal_after_cancel=false and 37 extra
renewals before cleanup. The final unattended rerun is the acceptance evidence.

The initial all-files hook reformatted four files and isort changed one file;
that run failed. After staging those changes, all hooks passed, including mypy
and secrets. A source revision hash in JSON triggered a secret-detector false
positive; marking its value with the git: provenance prefix resolved it without
an allowlist or hook bypass. Documentation/final commit and normal pre-push checks remain their
own gates until recorded at the final revision.

Still open: active tasks and domain/effect history, mixed/pruned history,
historical accounting completeness/invoices, prepared-source conflict repair,
artifact/config rollback, multi-worker migration, old UI transcript backfill,
remaining skill/provider/channel compatibility, old-engine/dependency deletion,
comparative workload completion and enterprise release qualification. This
scope is a first applied history migration, not completion of W5 or the goal.
