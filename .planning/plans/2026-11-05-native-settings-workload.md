# Native model settings, completion boundary and initial workload observations

This increment advances W0/W1/W2; it does not complete the execution contract.
The [frozen 20-scenario protocol](../fixtures/harness-workload-v1.yaml) retains
three repetitions and 60 measured turns per implementation. Only five scenarios
have been measured here: 15 turns per implementation, 45 still missing each.
The [primary report](2026-11-05-conversation-workload-sample.json) and
[UI observations](2026-11-05-conversation-ui-observations.json) retain failures.
[Additional smoke evidence](2026-11-05-native-model-settings-smoke.json) accounts
separately for restart, configuration and output-limit tests.

## Native configuration ownership

`NativeHarnessAction.model_settings` is persisted with `attribute(...)`
(`jvagent/action/native_harness/native_harness_action.py:88`). The adapter validates
names/types using a strict subclass of the public SDK `ModelSettings` TypedDict;
it does not copy its fields (`jvagent/harness/model_settings.py:8`). Validation
errors omit input/context values (`model_settings.py:26`). Startup validates
before traffic, and the factory validates again for direct callers or changed
configuration (`native_harness_action.py:100`, `jvagent/harness/native.py:54`).

Example native Action context, separate from legacy Orchestrator settings:

```yaml
model: openai:gpt-4.1
model_settings:
  temperature: 0.0
  max_tokens: 8192
  max_retries: 0
max_tokens: 100000
max_requests: 20
timeout_seconds: 180
```

`model_settings.max_tokens` limits generated output per request. The top-level
`max_tokens` is the run's aggregate usage ceiling. Settings default to an empty
dictionary, preserving SDK defaults when omitted. The same declared settings
reach root Agents, child Agents and official summarization requests
(`jvagent/harness/native.py:253`, `:298`, `:322`). SDK request-retry settings do
not replace agent/tool validation-retry limits. This adapter accepts common SDK
settings; provider-specific settings extensions are not yet a qualified surface.
Key/type validation does not prove that every provider accepts each value/range.

The tested Responses API rejected a one-token output setting with HTTP 400 and
an explicit minimum of 16. That attempt remains conservatively unknown-cost.
A 16-token setting generated an incomplete sentence. Before repair, the SDK's
response had normalized finish_reason=length and state=complete and JV delivered
it as success. The new public `before_output_process` capability rejects that
final candidate (`jvagent/harness/native.py:189`), including summaries and output
functions. It does not implement a continuation loop or infer completeness from
punctuation. This is a small JV complete-delivery policy adapter. Remove it
when the pinned library offers an equivalent configured completion requirement
for text, summaries and output functions with the same receipt guarantees; rerun
the retained adversarial and browser tests before removing it. JV maps the failure to an actionable reply-limit notice and reason
`output_token_limit` (`native_harness_action.py:349`).

Compaction now runs before parent request-start journaling (`native.py:249`).
The first summary regression exposed a false unknown parent attempt when a paid
summary failed before the parent called its model. Reordering official
capabilities removes that false attempt while preserving the child's usage.
Final deterministic tests cover text, summary and output-function boundaries.

References: [public ModelSettings](https://pydantic.dev/docs/ai/api/pydantic-ai/settings/)
and [capability hooks](https://pydantic.dev/docs/ai/capabilities/overview/).
Qualification is against pinned core 2.66.0 / Harness 0.66.0, not an assertion
about future documentation versions or every provider.

## Observed conversation slice

Same PostgreSQL 16 service, separate databases and isolated browser contexts,
same jvchat bundle, same synthetic SKILL body, same common instructions and
matching temperature/output ceilings. Both callers are disposable admins;
ordinary-member browser access is not qualified by this test. Legacy source is
detached main 43cbc885; its import path was verified before starting. Native was
the development worktree based on 6de0ff72. The parent remains in the evidence;
this cohort is not a comparison at one final committed candidate revision.
The privacy-error formatter changed/restarted native after its first turn;
completion/startup guards were added after the 30-turn cohort. Normal model
settings stayed unchanged. Retain these limitations rather than discard data.

| Observation, all 15 turns per implementation | Legacy | Native |
|---|---:|---:|
| Mechanical scenario checks | 9 pass / 3 fail / 3 pending | 11 pass / 1 fail / 3 pending |
| Recorded model calls | 23 | 15 |
| Total model tokens | 49,455 | 5,303 |
| Known estimated USD | 0.102108 | 0.012166 |
| Median backend Interaction seconds | 1.602005 | 1.566974 |
| Nearest-rank p95 backend seconds, n=15 | 4.569018 | 2.698692 |

All three legacy exact-text cases returned READY. with an extra period. One
native JSON-only response contained Markdown fences; the reporter intentionally
does not strip them before scoring. Supplied facts were recalled and missing
budget information was acknowledged in the observed turns. The three missing-fact cases per driver remain pending semantic review; a
missing-information phrase alone is not certification against fabrication. This
is mechanical checking plus working-agent inspection; independent quality review
is pending.

These are backend durations, not browser end-to-end latency or an SLA. All
observed outcomes, including format failures, remain in the totals. The APIs
also differ: legacy used its HTTPX Chat Completions adapter; native used the
library's Responses API. Native records the dated response model, while legacy
records the requested alias. This does not isolate an execution-loop effect or
prove identical resolved snapshots. A final paired benchmark must freeze the
candidate revision/API/model controls and measure browser latency. No whole-
workload optimization or 95% qualification gate is met by this slice.

Qualification environments contain 93 native and 126 legacy unique distribution
names. They use different test/transport profiles, so this is not a production
package-footprint claim. Raw Python LOC under Orchestrator / harness / native
Action is 13,309 / 2,391 / 0 on main, versus 17,305 / 3,580 / 375 in this worktree.
The transitional checkout is larger and still contains three execution paths.
Migration and legacy/pilot deletion are mandatory; no simplification is claimed.

## Additional actual-provider and browser evidence

The linked smoke JSON contains every extra persisted Interaction, root run,
response, terminal state, timing and usage. Configuration faults and a submit-
before-ready fixture mistake created no graph turn/model request; both are
recorded explicitly. Source blobs identify the final runtime used by the repaired
limit and recovery tests. No user deployment/graph was purged.

- Misspelled native setting: startup exits 1 without a listener. Counts stay at
  15 Interactions and 15 known model calls. Browser submission reports Failed to
  fetch; no provider call.
- Restored backend: both implementations recall CEDAR-47 and 18 November 2026.
  These two smoke submissions overlapped and are excluded from paired timings.
- One-token output: provider rejects the request; UI reports one unknown-cost
  attempt. It is not treated as a free model call or a length-stop success.
- Before repair at 16 tokens: clipped text was delivered. The evidence is retained.
- After repair at 16 tokens: failed/output_token_limit, closed/emitted error
  notice; one known call, 271 tokens and USD 0.000638 estimated, zero unknown-cost
  events. No clipped text is published as the final answer.
- After restoring 8192 and restarting: same conversation admits its accepted-input
  frontier, excludes the prior clipped model text and replies again. It returns
  READY. rather than exact READY, so recovery succeeds while formatting fails.

Whole disposable graphs now contain 16 legacy and 20 native Interactions. This
includes 25 + 19 known model calls, 60,699 known tokens and USD 0.126744 known
estimated cost, plus one native unknown-cost attempt. Legacy lacks explicit
unknown-cost counters; the report exposes that absence rather than asserting
zero. The deliberately rejected one-token conversation remains unpriced and is
not a completed reconciliation gate. Native per-request duration telemetry still
contains a default zero; the report uses persisted Interaction timestamps instead.

## Reproduction and remaining gates

`setup_conversation_workload.py` writes the [frozen app declarations](../fixtures/conversation-apps.yaml)
and SKILL into a new directory, copies only OPENAI_API_KEY, creates fresh private
admin/JWT credentials and refuses to overwrite an existing app. It does not
create databases, execute a model, migrate data or start a server. Supply two
separate disposable PostgreSQL DSN files. Never point the fixture at a user graph.

```bash
python scripts/setup_conversation_workload.py legacy /tmp/bench-legacy \
  --key-env /path/to/private-provider.env --dsn-file /path/to/legacy.dsn
python scripts/setup_conversation_workload.py native /tmp/bench-native \
  --key-env /path/to/private-provider.env --dsn-file /path/to/native.dsn
```

Run legacy from a detached checkout of the recorded main revision with its
qualified environment; verify `jvagent.__file__` there. A PYTHONPATH override
from the active repository's cwd does not establish isolation: cwd wins. Run
native from its recorded worktree/environment. Serve the same UI build on each
fixture's corresponding port and log in through isolated browser contexts using
the private fixture credentials. Wait for listener readiness before submitting.
The helper's default ports are 8132/3132 and 8133/3133. Change private declarations
when those ports are occupied; record the changes.

Execute frozen prompts through the UI, correlate exact Interaction IDs with UI
observations, and export those IDs through public jvspatial storage APIs. Keep
non-measured fault/smoke requests in a separate inventory, as this record does.
Recompute the offline report with:

```bash
python scripts/report_harness_workload.py \
  --manifest .planning/fixtures/harness-workload-v1.yaml \
  --legacy-export /path/to/legacy-measured-interactions.json \
  --native-export /path/to/native-measured-interactions.json \
  --ui-observations .planning/plans/2026-11-05-conversation-ui-observations.json \
  --output /tmp/workload-report.json
```

The reporter rejects duplicate/mismatched/unobserved input rows, preserves missing
measurements and usage, and cannot certify the independent quality or release
gates. Tests exercise those failures and credential isolation/overwrite refusal.
Remaining adapters cover retrieval/research fixtures, artifacts, interviews,
visitor/MCP/delegation/proactive workloads, long context and injected recovery.
Broader providers, ordinary-member access, native distributed ownership, migration,
legacy removal and release evaluation remain open.

Two issues were observed in the served bundle and need reproduction against a
fresh UI build before attributing them to current source: a 422 login validation
object produces React error 31/blank UI, and New Conversation retains previous
messages visually until reload even though the server uses fresh session IDs.
The model did not receive prior-session history in the repeated fresh-session
checks. Machine-facing output workflows should gain explicit SDK typed output
contracts; the raw conversation-string format failures are not repaired here.
The deprecated SpendLimits.on_spend shim and missing native request timings also
need consolidation onto official events; no second execution engine is warranted.
