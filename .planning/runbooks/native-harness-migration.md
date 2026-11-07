# Native harness configuration migration

This is an offline configuration-candidate tool, not a graph upgrader. The
[execution plan](../plans/2026-11-04-harness-rebuild-execution.md) still governs
compatibility, state migration, cutover and engine deletion. Existing released
artifacts remain the rollback boundary. Do not point an unqualified candidate at
an existing production graph.

## Plan and review

Use the qualified native dependency profile and the app's existing skill assets:

```bash
# Preserve an existing finite skills list and its app/library source policy.
jvagent path/to/app harness-migrate --agent namespace/bot
# Or explicitly select installed skills when no finite set was declared.
jvagent path/to/app harness-migrate --agent namespace/bot --skill answer \
  --skill research --output /private/staging/bot-native.yaml
```

Global `--purge`, `--update`, `--source` and `--merge` combinations are refused
before mutation processing, regardless of flag order
([`main.py:176`](../../jvagent/cli/main.py#L176)).
Without `--output`, nothing is written. With it, only a new candidate file is
created, with mode 0600; existing files and symlinks are refused. The source YAML,
skills, environment files and graph are not rewritten. Reports omit setting values;
the candidate retains other Action configuration and may contain private data.
Keep it in an operator-controlled directory. Exit 2 means blocked; exit 0 means
candidate generated/planned or already native, not release qualification.
Implementation: [`harness_migrate.py:491`](../../jvagent/cli/harness_migrate.py#L491).

The planner reads literal YAML. Other Action placeholders remain authored; it
does not resolve credentials into a report. Model/limit placeholders requiring
resolution block conversion. Unknown execution settings also block, including
custom legacy prompts, fallback and active channel/flow controls. Known engine
heuristics retire explicitly in the report, rather than being copied into another
loop. A finite declared skill set is preserved; explicit repeated `--skill` values
must match that set when present. Dynamic `-all`/wildcard selectors require an
explicit declaration migration. The command does not implement another router.

Native runtime also supports an explicit multi-skill catalog through
`skill_directories`; see [catalog configuration and policy](native-skill-catalog.md).
The converter preserves finite declared sets as catalogs; an explicit single
selection without such a set remains a primary skill. Neither mode proves that
dynamic selectors or historical router state have been migrated.

Unmapped controls and provider policy are checked in
[`harness_migrate.py:283`](../../jvagent/cli/harness_migrate.py#L283).

## Defined mappings

| Declaration | Candidate behavior |
|---|---|
| OpenAI model / `openai/` LiteLLM model | Native `openai:` provider; other provider mappings remain unqualified |
| `model_temperature`, positive `model_max_tokens` | Public native model settings; zero/absent completion ceiling selects a reported 8192-token proposal |
| `activation_budget` | Native request ceiling; explicit zero remains clamped to one, as in the old loop; absent selects a reported 20-request proposal |
| `max_concurrent_tools` zero/one or omitted legacy default | Public SDK `tool_execution_mode: sequential`; wider numeric ceilings remain blocked, not widened to parallel |
| `max_duration_seconds` | Native timeout; zero/absent selects a reported finite 180-second proposal |
| Positive turn/conversation dollar limits | Same numeric limits on native run/conversation spend; zero/absent selects reported USD 2/20 proposals |
| Pilot request/tool/total/output/runtime ceilings | Native public usage limits and timeout, including the pilot's implicit defaults; mixed driver controls block |
| Agent alias/role and `system_prompt_extra` | Literal trusted instructions; legacy prompt templates are not copied |
| Other Actions and their nested settings | Preserved in order; runtime skill/tool/channel qualification remains required |
| Selected simple model service | Proposed retirement only with reviewed consumers, standard OpenAI credentials/endpoint and no custom retry policy; unknown consumers/settings block |
| Finite `skills` / `skills_source` | Official deferred catalog preserving app-over-library precedence; no silent subset selection |
| `max_statement_length` | Soft instruction when positive; no character truncation of generated output |
| Known light-model, synthetic ack/progress, lean pre-surface and turn-window controls | Explicit retirement into single-model, native-event, official-Skills and SDK-compaction behavior; report requires review |
| Typed `block_raw_tool_invocation` boolean | Explicit retirement of legacy selection prompt/auto-promotion; SDK skill ownership and host tool permission remain |
| `tool_servers: "-all"` or implicit default with MCP Actions | Retire the selector only with reviewed native tool declarations on enabled MCP Actions; restricted/empty/invalid selectors stay blocked |
| Typed inactive pilot settings under the legacy driver | Explicit retirement; active legacy budgets migrate independently |
| Empty/disabled channel/tool/vision/flow controls | May retire only when exactly empty/disabled; active policies block until qualified |
| Reviewed default `vision: true` + default OpenAI VisionAction + primary GPT-4.1 | Declare native image MIME policy and propose removing VisionAction; independent model/tool/description-artifact semantics retire explicitly. Custom callers/settings and dependent skills block. See [qualification](../plans/2026-11-06-native-vision-migration.md) |

Mapping/default construction: [`harness_migrate.py:359`](../../jvagent/cli/harness_migrate.py#L359).

See [native tool execution](native-tool-execution.md) for scheduling modes and
the difference between preserving a concurrency ceiling and reproducing legacy ticks.

See [control retirement and response-policy qualification](../plans/2026-11-06-native-controls.md).
Retiring the MCP selector neither enables a disabled Action nor qualifies its
transport/tools. Native replies use [declared response policy](native-egress.md)
instead of implicit legacy response defaults; explicit scrub rules still apply.

The candidate uses app-relative skill directories or portable `library:<directory>`
references to installed JV assets, resolved independently of process cwd. No skill
files are rewritten. Public official Skills validation checks names/invocability,
using the same JV policy projection as execution. Unsupported scripts, naming,
SOP policy and Action bases block or require explicit migration; runtime Action/tool
prerequisites and historical state still require separate qualification.
The command requires the native-harness dependency profile and a synchronous CLI
context. Library references cannot traverse paths or follow directory/SKILL.md
symlinks out of the package asset boundary. Relative app paths also refuse
traversal/symlink escape;
explicitly absolute paths remain supported for existing configurations.
See [`skill_assets.py:34`](../../jvagent/harness/skill_assets.py#L34).

Native interview skills use the explicit `action:jvagent/interview` base. Hooks
require an enabled canonical InterviewAction and a host-reviewed
`native_hook_digests` entry; the planner neither imports app code nor grants that
approval. Review the SOP, callback source, imported dependencies and effects as
host plugin code, then compute the digest with
`jvagent.action.interview.native_hooks.native_hook_digest(Path(skill_directory))`
and configure it under that Action's context. Any source change requires review
and Action reload. This is not a Python sandbox or approval for external effects.
See [qualification and remaining limits](../plans/2026-11-06-native-signup-hooks.md)
and [`harness_migration_skills.py:61`](../../jvagent/cli/harness_migration_skills.py#L61).

Native `max_tool_calls` defaults to 40. Optional `max_output_tokens` defaults to
None, preserving the existing native profile; a migrated pilot carries its
cumulative output ceiling explicitly. These are passed to public Pydantic
`UsageLimits`, alongside request and total-token ceilings, rather than checked
by another execution loop. See [`native.py:487`](../../jvagent/harness/native.py#L487)
and [`NativeHarnessAction:115`](../../jvagent/action/native_harness/native_harness_action.py#L115).

## Rehearse before cutover

Review every mapping, finite-default proposal and blocker. Preserve the original
configuration and released artifact. Install the candidate only in an isolated
copy with a fresh disposable graph, keeping its SKILL.md assets and modular
Actions. Bootstrap, then exercise actual model/tool/channel workflows in the
browser and read back outcomes, task state and accounting. A repeated plan of a
native declaration returns `already_native` without changing it.

The tool does not reconcile stored Orchestrator Actions, in-progress skills,
old conversation spending, pilot checkpoints, unknown effects or delivery
receipts. Merge sync can preserve old graph configuration; a generated YAML is
not authorization to purge or destructively sync a production graph. These
remain blocking requirements for the final migration/cutover implementation.

Bounded rehearsal and explicit remaining cases:
[2026-11-05 migration evidence](../plans/2026-11-05-native-migration.md).

Latest finite-catalog/retirement rehearsal and current reference blockers:
[configuration cutover evidence](../plans/2026-11-05-native-config-cutover.md).
The CLI may emit environment-loading logs before its JSON report; use the returned
Python planner mapping for direct structured integration or separate the logging
prefix when capturing CLI stdout. Candidate YAML is written separately and privately.

## Retained-state preflight

An optional offline state check complements the configuration plan:

```bash
jvagent path/to/app harness-migrate --agent namespace/bot \
  --state-snapshot /private/staging/conversation.json
```

The input is a private JSON object with exactly `conversation` (one persisted
jvspatial Conversation row) and `interactions` (its retained Interaction rows).
Each row uses the existing `{id, entity, context}` envelope. Capture from the
intended graph under operator-controlled quiescence; do not publish the raw
snapshot or paste it into chat. It may contain transcript, credentials/capability
state and prompts. The tool does not connect to the graph, acquire a lock, verify
live ownership, reconstruct graph edges or approve a migration.

Snapshots are bounded to 2 MB and 64 retained Interactions. Duplicate JSON keys,
nonfinite constants, malformed/scope-mismatched rows and ambiguous accounting
are refused or reported as blockers. Reports contain a source digest, aggregate
verified receipts, observed/unverified model-event counts and a distinct legacy
ledger total. Missing/invalid receipts and zero estimates do not become free
usage. The ledger is not relabelled as a provider invoice. Historical/pruned
usage still needs reconciliation. Only explicitly typed per-call cost records
with reviewed source markers qualify for the verified receipt subtotal; other
old shapes require a separate migration rather than current-price reinterpretation.

State blockers also prevent writing the YAML candidate. With no blockers, state
status is still `review_required` and `cutover_authorized` is always false. A
configuration-only invocation can still produce its candidate without a snapshot.
This does not add an apply operation or change native admission, which continues
to refuse unqualified legacy history. See the
[preflight implementation and evidence](../plans/2026-11-05-native-state-preflight.md).

## Administrator-approved published-text import

The separate live operator API now supports a restricted historical cohort. It
does not make the offline compiler apply state. Rehearse on an isolated clone
first, retain the original graph/configuration/artifact and review every refusal.
See [actual browser/SQL evidence](../plans/2026-11-06-native-history-import.md).

All paths below are relative to the server API prefix (normally `/api`), require
an authenticated administrator and use the installed native Action ID plus a
historical Interaction belonging to its Agent. Operator identity is established
by the server. These routes are not model tools. Source:
`jvagent/action/native_harness/endpoints.py:29`, `:181`.

1. GET `/actions/{action_id}/native-history/{interaction_id}` to inspect current
   facts under the conversation lock. If timestamps have no timezone, resolve
   their meaning explicitly. Only an approved UTC interpretation is currently
   supported, via `?naive_timestamp_zone=UTC`. Do not select UTC to bypass an
   unresolved timezone. Inspect returns digests, counts, owner/session scope and
   the distinct old ledger, without transcript content.
2. Reconcile any pruned/missing usage independently. Choose a conservative USD
   estimate at least as large as the retained ledger, tokens at least the reported
   minimum and requests at least the observed model events. The API cannot prove
   lifetime accounting completeness or invoice costs. Zero dollars with observed
   requests is refused. Source: `jvagent/harness/history_import.py:387`.
3. POST `/actions/{action_id}/native-history/{interaction_id}/apply` with the
   exact digest and an `approval` wrapper. Example shape, using values from your
   reviewed inspection rather than the illustrative numbers:

   ```json
   {
     "approval": {
       "digest": "sha256:<64 lowercase hexadecimal characters>",
       "usd": "0.05",
       "tokens": 15133,
       "requests": 7,
       "naive_timestamp_zone": "UTC",
       "evidence": "Operator-controlled source and accounting review reference",
       "reason": "Approved ordinary text import with estimated historical spend"
     }
   }
   ```

   Omit naive_timestamp_zone when source timestamps are already timezone-aware.
   Do not send actor, transcript, receipt authority or provider credentials.
   If interrupted, retry the identical approval; prepared intent and the official
   spend marker prevent duplicate debits. Changed source/approval requires separate
   operator reconciliation rather than replacement. Source:
   `jvagent/harness/history_import.py:387`.
4. Exercise an actual-model continuation in the browser. Read back the new native
   result, history source and shared spend total; retain all original records.
   The migration imports only canonical user/assistant text. Original system
   instructions, old tool calls and provider metadata are not imported or replayed.
   Source: `jvagent/harness/history_import.py:516`.
5. Before any new execution or spend, POST
   `/actions/{action_id}/native-history/{interaction_id}/rollback` with
   `{"approval":{"digest":"sha256:<approved digest>"}}` if the import must be
   withdrawn. This restores the originally empty native counter and preserves an
   audit receipt. Duplicate rollback is idempotent. New execution, changed
   counters, reapplication after rollback and whole-artifact rollback require
   separate handling. Source: `jvagent/harness/history_import.py:576`.

Admission currently requires completed default-channel ordinary text, settled
reply delivery, no task/domain context or existing native spend, at most 64 legacy
turns and no effects beyond reviewed reply/use_skill traces. Up to 32 qualified
nonexecuting notices can follow the legacy frontier. Malformed, mixed or unqualified
state fails closed; do not purge it to force adoption. Source:
`jvagent/harness/history_import.py:69`.

Cold-browser conversation discovery remains unqualified. Bounded approved text
and completed native user/assistant pairs now restore through the existing
authorized subscription; historical tool/cost backfill is separate. See the
[actual cache-loss/browser/SQL rehearsal](../plans/2026-11-06-native-history-readback.md).
The rehearsal selected an existing session by a local index entry without writing
transcript messages. Multi-worker migration, active task/
effect transfer, prepared-source conflict repair and released-artifact rollback
are separate open gates. This operation does not authorize legacy engine deletion.

The converter maps strict boolean `planning` into official SDK planning;
`planning: true` requires explicit review of retained legacy plan/task state.
This is configuration mapping, not stored-task conversion or an alias for the
old `plan`/`update_plan` tools. See the
[fresh-graph rehearsal](../plans/2026-11-06-native-planning.md) and
`jvagent/cli/harness_migrate.py:287`.

Preserve the configured SDK workspace during supported upgrades. Persisted native
messages reference that workspace; copying the graph to another application path
does not authorize rebinding it. The current native profile has no workspace
relocation approval operation. A moved workspace is refused by the library before
execution; do not rewrite old messages or grant filesystem access based on a
historical reference. Operator-reviewed relocation and recovery of the resulting
pre-registration failed frontier remain separate migration work. Boundary
regression: `tests/harness/native/test_context.py:42`.

Clock service migration retires `tool_tier`. Standard/full (including the old
standard default) explicitly retain or add the existing `jvagent/agent_utils`
Action in the new candidate; minimal adds none. Disabled/ambiguous declarations
and minimal with an active clock service require operator review. Source YAML
is preserved. The clock keeps `get_current_datetime`, with no model parameters,
and uses fresh configured app time. Invalid configured timezones now refuse
rather than silently falling back. Skill ownership and host permissions still
apply. Sources: `jvagent/cli/harness_migrate.py:370`,
`jvagent/action/agent_utils/agent_utils.py:19`, `jvagent/core/clock.py:8`.
See [clock and schema qualification](../plans/2026-11-06-native-clock.md).

Existing Action JSON Schemas are validated through the SDK public
`args_validator` callback because `Tool.from_schema` skips built-in validation.
The thin bridge uses standard JSON Schema validation, refuses external reference
retrieval and returns generic errors without argument values; it does not coerce
inputs. Local references work. Remove this adapter when the SDK supplies the
equivalent validation for schema-defined tools, then remove its dependencies if
no other consumer needs them. Existing permissive declarations remain permissive;
this does not qualify every Python annotation or Action-specific constraint.
Sources: `jvagent/harness/native.py:169`, `jvagent/harness/tool_schema.py:14`,
`jvagent/tooling/signature_schema.py:143`.
