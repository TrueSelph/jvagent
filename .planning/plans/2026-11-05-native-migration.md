# Native configuration candidate and preserved usage ceilings

Parent `9b217d28360a4bb979a5e9fb23a93ce8bf620c59`; all nine parent CI checks pass.
This starts configuration migration toward cutover. It does not migrate stored
graphs, switch the product default, or remove the old engine implementations.

## Implementation and contract

The new offline `harness-migrate` command emits a redacted plan and optionally a
new mode-0600 YAML candidate. It never writes the source or graph; output
overwrite/symlink, source traversal, malformed/oversized input, mixed owners,
unknown execution controls and unqualified provider policy fail closed.
See [`harness_migrate.py:84`](../../jvagent/cli/harness_migrate.py#L84),
[`candidate writer:365`](../../jvagent/cli/harness_migrate.py#L365) and
[operator runbook](../runbooks/native-harness-migration.md).

The selected simple OpenAI/LiteLLM model service can be retired in the candidate
only with the reviewed consumer/default-credential/endpoint/retry boundaries.
Other Actions, nested configuration, identity and SKILL.md files are retained.
Custom prompts/gearing/fallbacks/channel controls are not discarded. The existing
reference agent produces [31 explicit blockers](2026-11-05-native-migration-reference-blockers.json);
this tool does not claim to migrate that whole profile yet.

Pilot tool and cumulative output ceilings exposed a missing configurable native
surface. NativeHarnessAction now forwards `max_tool_calls` (default 40) and
optional `max_output_tokens` (default None) through public Pydantic UsageLimits.
No new counter, dispatcher or execution engine is added
([native runner:406](../../jvagent/harness/native.py#L406),
[Action invocation](../../jvagent/action/native_harness/native_harness_action.py#L394)).
The converter preserves explicit pilot caps and their implicit defaults. Explicit
legacy activation_budget=0 remains a one-request ceiling; it is not widened to
the native default. Former disabled/omitted duration/dollar limits and dynamic
completion ceilings produce explicit finite-default proposals for review.

Candidates use portable app-relative skill paths. Resolution is independent of
cwd and rejects relative escape, including symlinks; existing explicit absolute
paths remain supported ([resolver:34](../../jvagent/harness/skill_assets.py#L34)).

## Real migration/browser rehearsal

New disposable app `/private/tmp/jvagent-native-migration-smoke`, fresh PostgreSQL
`jvagent_native_migration_20261105`, backend 8138, source-built reference UI 3136,
isolated browser context `native-config-migration`. Actual `openai:gpt-4.1`.
The CLI ran from the repository cwd rather than the app directory.

1. Plan and generate from a synthetic [legacy declaration](2026-11-05-native-migration-source.yaml)
   containing Orchestrator, standard OpenAI service, MCP and Reply Actions.
   The [safe CLI report](2026-11-05-native-migration-plan.json) records mappings,
   source hash, proposed service retirement and output creation. A SQL assertion
   before first bootstrap found no node table. No old graph was upgraded.
2. The fixture driver installed the [generated candidate](2026-11-05-native-migration-candidate.yaml)
   only in this new app, preserving the original source separately. Bootstrap
   created one NativeHarnessAction, MCPAction and ReplyAction, with zero
   Orchestrator/OpenAI model-service nodes. [Fresh graph configuration](2026-11-05-native-migration-graph.json)
   records persisted limits and the app-relative skill path.
3. The first paid browser request contained duplicated text from an automation
   selection mistake; its MCP lookup and correct result still succeeded. It
   remains in the [SQL receipt](2026-11-05-native-migration-final-smoke.json).
   A new conversation then sent the clean query: `NATIVE-MIGRATION-CHECK: Look
   up REHEARSAL-47 using the installed skill and tell me its room and start time.`
4. The [browser](2026-11-05-native-migration-browser.json) showed one MCP tool
   group and the correct Cedar/14:30 result, plus model, two attempts, 749 tokens
   and 2.2s at the model boundary. HTTP resource duration: 3.6129s; persisted
   Interaction: 3.488349s. First Interaction: 4.977780s / HTTP 5.158s.
5. Entire cohort: four provider requests, 1,560 provider-reported tokens and
   USD 0.003660 estimated cost. Both Interactions are closed/emitted/delivered;
   SDK records and conversation spending reconcile with no remaining reserves.
   The source-built frontend is not packaged-client qualification.
6. Repeating the command returns `already_native`; [fresh SQL after repeat](2026-11-05-native-migration-rerun-smoke.json)
   has identical Interactions, task state and spending. Source/candidate hashes
   and unchanged-source unit tests distinguish tool behavior from the fixture
   driver's explicit installation step.

The runtime module probe shows no legacy execution modules. Actual native
execution/resolution source hashes are captured in the SQL/runtime receipts.
Native execution/resolution hashes agree between startup, exports and current
source. The CLI reader received bounded-read hardening after the paid cohort;
the rerun export records that final helper variant separately from startup.
Final CLI tests and the repeated offline command cover the final reader. Native
runtime sources did not change after the model tests.

The first observation wrapper failed before importing jvagent; the observer was
fixed and the disposable process cleanly restarted before any model request.
The private app's redundant unsupported auth YAML stanza was removed; token
lifetime remains configured through the environment. An initial DOM-only fill
did not arm the composer, and a click timed out without an interaction request;
actual keyboard input sent the paid requests. These setup/automation defects are
not reported as model or harness successes.

## Regression and publication gates

- CLI/config/native-runner/skill-path slice: 133 pass on each
  [Python 3.11](2026-11-05-native-migration-cli311.txt) and
  [Python 3.12](2026-11-05-native-migration-cli312.txt).
- Native/host/read-tool/real-PG suites: 367 pass on each
  [Python 3.11](2026-11-05-native-migration-native311.txt) and
  [Python 3.12](2026-11-05-native-migration-native312.txt).
- The first broad suites found [19 stale fixture failures](2026-11-05-native-migration-initial-tests.txt):
  SimpleNamespace test doubles lacked the new Action attributes. Those fixtures
  now model the actual fields; the public runner is not weakened with fallbacks.
- Isolated process mutations, without changing repository/runtime files, make
  [the output-ceiling regression fail](2026-11-05-native-migration-output-mutation.txt)
  when the SDK limit is removed, and [the unknown-setting regression fail](2026-11-05-native-migration-settings-mutation.txt)
  when a legacy prompt is silently accepted and discarded.
- The first all-files hook pass formatted new files and caught a tuple-length
  typing error; it was fixed and subsequent all-files checks passed. Final
  staged all-files checks pass cleanly; normal full pre-push and exact-head CI
  remain separate publication gates.

Still open: the reference profile's listed controls, other providers/custom
consumers, broader skill and Action compatibility, state/credential/accounting
migration, abandoned/uncertain runs and effects, upgrade/rollback, default native
cutover and actual legacy/pilot implementation/dependency deletion. Actual-model
tiny-output-cap failure settlement is not qualified by the FunctionModel limit
test. No enterprise, complete migration or performance claim follows here.
