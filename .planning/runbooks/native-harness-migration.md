# Native harness configuration migration

This is an offline configuration-candidate tool, not a graph upgrader. The
[execution plan](../plans/2026-11-04-harness-rebuild-execution.md) still governs
compatibility, state migration, cutover and engine deletion. Existing released
artifacts remain the rollback boundary. Do not point an unqualified candidate at
an existing production graph.

## Plan and review

Use the qualified native dependency profile and the app's existing skill assets:

```bash
jvagent path/to/app harness-migrate --agent namespace/bot --skill answer
jvagent path/to/app harness-migrate --agent namespace/bot --skill answer \
  --output /private/staging/bot-native.yaml
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
Implementation: [`harness_migrate.py:365`](../../jvagent/cli/harness_migrate.py#L365).

The planner reads literal YAML. Other Action placeholders remain authored; it
does not resolve credentials into a report. Model/limit placeholders requiring
resolution block conversion. Unknown execution settings also block, including
custom legacy prompts, gearing, fallback and channel controls. Translate their
intent to skills or native capabilities explicitly before removing them. One
installed skill must be selected; this does not migrate the entire old router.

Unmapped controls and provider policy are checked in
[`harness_migrate.py:154`](../../jvagent/cli/harness_migrate.py#L154).

## Defined mappings

| Declaration | Candidate behavior |
|---|---|
| OpenAI model / `openai/` LiteLLM model | Native `openai:` provider; other provider mappings remain unqualified |
| `model_temperature`, positive `model_max_tokens` | Public native model settings; zero/absent completion ceiling selects a reported 8192-token proposal |
| `activation_budget` | Native request ceiling; explicit zero remains clamped to one, as in the old loop; absent selects a reported 20-request proposal |
| `max_duration_seconds` | Native timeout; zero/absent selects a reported finite 180-second proposal |
| Positive turn/conversation dollar limits | Same numeric limits on native run/conversation spend; zero/absent selects reported USD 2/20 proposals |
| Pilot request/tool/total/output/runtime ceilings | Native public usage limits and timeout, including the pilot's implicit defaults; mixed driver controls block |
| Agent alias/role and `system_prompt_extra` | Literal trusted instructions; legacy prompt templates are not copied |
| Other Actions and their nested settings | Preserved in order; runtime skill/tool/channel qualification remains required |
| Selected simple model service | Proposed retirement only with reviewed consumers, standard OpenAI credentials/endpoint and no custom retry policy; unknown consumers/settings block |

Mapping/default construction: [`harness_migrate.py:244`](../../jvagent/cli/harness_migrate.py#L244).

The candidate uses an app-relative skill directory, resolved independently of
process cwd. Relative traversal/symlink escape outside the app is refused;
explicitly absolute paths remain supported for existing configurations.
See [`skill_assets.py:34`](../../jvagent/harness/skill_assets.py#L34).

Native `max_tool_calls` defaults to 40. Optional `max_output_tokens` defaults to
None, preserving the existing native profile; a migrated pilot carries its
cumulative output ceiling explicitly. These are passed to public Pydantic
`UsageLimits`, alongside request and total-token ceilings, rather than checked
by another execution loop. See [`native.py:406`](../../jvagent/harness/native.py#L406)
and [`NativeHarnessAction:113`](../../jvagent/action/native_harness/native_harness_action.py#L113).

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
