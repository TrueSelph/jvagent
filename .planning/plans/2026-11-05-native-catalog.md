# Native multi-skill catalog qualification

Parent revision: `b2e4fd30`. All nine remote checks passed at that parent.
This increment adds explicit native catalog configuration, not a replacement
router. Official Skills owns catalog validation and deferred loading; the SDK
owns execution, tool scheduling and persisted activation. No dependency is added.
See [operator configuration](../runbooks/native-skill-catalog.md) and the
[migration inventory](2026-11-04-harness-compatibility-inventory.json).

## Runtime and policy

`NativeHarnessAction.skill_directories` accepts explicit app-relative skill
directories, mutually exclusive with the existing automatically loaded primary
`skill_directory` (`native_harness_action.py:106`, `:291`). Up to 32 members are
admitted through JV declaration checks and the official loader. Invalid,
duplicate, disabled or unavailable members fail before a model request.
`jvagent/harness/skill_catalog.py:24` owns declaration facts and policy only.

Public SDK `active_capability_ids` determines whether an owning skill is loaded.
`native.py:223` filters function tools; `native.py:89` independently checks Action
dispatch and `mcp.py:53` checks MCP dispatch. Current host access remains a separate
required check. A raw tool name cannot load its owning skill. The existing
single-skill/delegation path is retained without another execution engine.

Research output is offered only for the current run's research invocation. A
current research load or active research-tool call latches its evidence obligation
through the run; plain-text final output cannot bypass it (`native.py:239`).
The run-local mutable policy state survives copied child-hook contexts and does
not leak into concurrent/subsequent runs (`skill_catalog.py:105`). It never edits
the SDK's persistent activation state. Provider length limits still terminate
before output-function processing rather than becoming a paid grounding retry.

Catalog proactive tasks must explicitly name one configured skill; unknown or
unlisted siblings cannot expand the catalog (`native_harness_action.py:306`).
Their selected skill enters the same factory. JSON and real-PostgreSQL tests
exercise successful/failed/cancelled task outcomes with deferred saves on/off.
Those task models are FunctionModel fixtures; an actual-provider catalog timer
dispatch is not claimed by this round.

## Browser-discovered defect and correction

Fresh isolated app `/private/tmp/jvagent-native-catalog-smoke`, PostgreSQL database
`jvagent_native_catalog_20261105`, backend 8140, source-client preview 3136.
The initial three actual GPT-4.1 turns delivered greeting, MCP lookup and sourced
research. The fourth ordinary greeting failed because the first policy adapter
treated library activation from old history as a new research obligation.
The SDK intentionally retains loaded capabilities and does not support unload.
The fix observes current-run tool hooks instead of changing library state.

The [initial browser receipt](2026-11-05-native-catalog-before-fix-browser.json)
and [SQL export](2026-11-05-native-catalog-before-fix-smoke.json) retain that
failure, including its two paid requests and 5221 reported tokens. The initial
four-turn cohort used 10 requests, 12054 tokens and USD 0.016740 estimated.
The pre-fix export was read while source edits were beginning; runtime startup
module hashes, rather than export-time source hashes, identify that tested code.
The backend PID/command was verified before SIGTERM, and the original process
was confirmed exited before a fresh process started. No model transport or
execution function was patched for either browser cohort.

## Final actual-provider cohort

All five final turns ran in one fresh conversation on the corrected source.
See [browser UI/resources](2026-11-05-native-catalog-final-browser.json) and
[persisted outcomes/SDK records/usage](2026-11-05-native-catalog-final-smoke.json).
Runtime startup hashes match the four changed execution modules, and observation
finds no legacy execution modules loaded.

| Scenario | Outcome | Provider requests | Reported tokens | Estimated USD | Persisted duration s |
|---|---|---:|---:|---:|---:|
| Greeting through conversation skill | Delivered | 2 | 580 | 0.001322 | 4.198183 |
| MCP REHEARSAL-47 lookup | Delivered; Cedar, 14:30 | 3 | 1528 | 0.003446 | 4.736440 |
| Research public Python description page | Delivered; fetched-source citation | 3 | 4742 | 0.008608 | 6.033685 |
| Ordinary greeting after research | Delivered; Good night! | 1 | 2366 | 0.004762 | 2.133544 |
| Fresh research after greeting | Delivered; fresh fetch and citation | 3 | 9857 | 0.009196 | 7.910731 |

Final cohort: five delivered turns, 12 provider requests, 19073 tokens,
USD 0.027334 estimated. The second research run reuses the library's loaded skill,
fetches fresh evidence and rejects a plain-text model candidate before a typed
grounded finish. Its third request is a validation retry, included in all totals.
The browser displays skill loads, MCP/fetch tools, citations, attempts, tokens
and model-boundary timing; HTTP resource durations are separately recorded.

Fresh PostgreSQL readback reconciles final conversation totals exactly, with
zero unpriced requests and zero outstanding reserves. The first failed cohort
remains failed/delivered as originally observed. Whole round: eight delivered
turns out of nine, 22 requests, 31127 tokens, USD 0.044074 estimated. Do not exclude
the initial failure from a whole-round denominator. MCP audit contains two
REHEARSAL-47 reads, one per lookup across the two cohorts. No currency value is
claimed as a provider invoice. Snapshot exports retain metadata, counts and
message digests while omitting repeated prompt/page payloads; this is not a full
checkpoint export/replay artifact.

## Automated validation

Native/host/read-tool/real-PostgreSQL suites pass 394 cases on Python 3.11 and
394 on Python 3.12. New regressions cover hidden direct calls, independent access
denial, deferred loading, research plain-text bypass, history-bearing ordinary
follow-up, concurrent/child-hook obligation scope, bad-member preflight,
ambiguous selection and clipped-output termination. MCP lifecycle/error/deny
and proactive/real-PG tests now also run in catalog mode.

Isolated activation-check removal fails both authorization test cases; isolated
research-output-guard removal fails both fresh/history cases. Mutations run in
separate test processes and do not change the browser backend. Initial test
failures are retained in local logs: old error-prefix expectations, an incomplete
synthetic fetch receipt, two denials exhausting the SDK's one-retry allowance,
old effect-count assertions omitting the SDK load effect, and seven broader-suite
failures from a moved policy test hook and a new helper parameter. These were
corrected without weakening the intended permission or output assertions.

All-files hooks pass clean on staged source. Final all-files/commit/full-push and
new-head CI remain separate publication gates. No frontend source changed.

## Remaining gates and simplification

The catalog closes the single-primary-skill runtime limitation for the tested
JV read/research/task profiles. It does not migrate the old `skills`/
`skills_source` selectors, wildcard names, script-backed skills or historical
graph/tasks/usage. Reference migration still reports 31 blockers. Channel-specific
catalog composition, arbitrary external writes, actual-provider catalog scheduled
tasks and broader provider/deployment recovery remain unqualified.

Research enforcement applies to a current skill invocation, not inferred user
intent or every factual answer from a general agent. Already-loaded skills remain
active per library semantics, with live host permission checks on every tool call.
No runtime module is deleted in this increment; the new catalog replaces no
remaining legacy caller until migration passes. Skill loading adds provider
round trips in these cases. This is compatibility evidence, not an optimization,
95-percent workload qualification, full comparison, soak or enterprise claim.
