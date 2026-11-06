# Native skill catalog

Script-backed `library:triage` can use the optional official Docker/Shell path
declared by CodeExecutionAction. Named host permission and explicit native image
policy are required; no legacy subprocess fallback is permitted. This does not
qualify artifact delivery, persistent storage or all Claude-standard skills.
See the [development profile](native-script-sandbox.md) and
`jvagent/action/code_execution/code_execution_action.py:84`.

Optional `context.planning: true` enables the official SDK `write_plan` and
`read_plan` tools with a scoped graph-backed [native plan](../GLOSSARY.md#native-plan).
Keep host permissions explicit; skill metadata does not authorize plan writes.
This root-run checklist does not schedule or execute its listed work, replace
domain tasks, or import retained legacy plans. See the
[qualification and migration boundary](../plans/2026-11-06-native-planning.md).
Sources: `jvagent/harness/native.py:423`,
`jvagent/action/native_harness/native_harness_action.py:376`.

Use an explicit catalog when an agent needs multiple skills. The official
[Skills capability](https://pydantic.dev/docs/ai/harness/skills/) owns discovery,
validation, deferred instruction loading and capability activation. JV translates
its declarations and applies permission and output policy; it supplies no skill
router or second execution loop.

```yaml
- action: jvagent/native_harness
  context:
    model: openai:gpt-4.1
    skill_directories:
      - agents/example/assistant/skills/conversation
      - agents/example/assistant/skills/records
      - agents/example/assistant/skills/research
```

Configure either `skill_directories` or the existing `skill_directory`, never
both. The singular form continues to load that primary skill automatically.
The catalog form offers the configured skills for library-managed selection.
Each directory must contain its own reviewed SKILL.md; roots are explicit and
bounded to 32 entries. `library:research` selects the installed package asset
portably; references to one directory cannot traverse paths or escape through
package directory/SKILL.md symlinks (`skill_assets.py:56`). App-relative paths retain the existing containment rules.
Directory/name mismatches, duplicate names and unsupported projected metadata
fail admission. Exact channel allow/deny lists omit ineligible catalog members;
an unavailable primary or empty eligible catalog refuses before model work.
Eligible members must satisfy Action/tool prerequisites and official loader
preflight. Omitted members still require full qualification before later use.
See `jvagent/harness/skill_catalog.py:24` and
`jvagent/harness/skill_assets.py:34`.

The host applies the same channel projection before binding fresh script assets.
Channel selection grants no host permission or authenticated adapter origin and
does not remove previously staged user files. Forced SDK bypass, independent
permission and real-provider browser evidence:
[channel qualification](../plans/2026-11-06-native-channel-catalog.md).

Tools are exposed only when an owning skill is active according to the public
SDK context. Calling a hidden tool by name does not activate a skill. The SDK
filters its callable tool surface; JV additionally checks ownership at Action
and MCP dispatch, then applies current host AccessControl independently.
See `jvagent/harness/native.py:83`, `jvagent/harness/native.py:219` and
`jvagent/harness/mcp.py:52`. Skill metadata cannot grant host permissions.

Library skill activation intentionally survives conversation history. JV does
not unload skills or edit SDK load records. Research evidence obligations are
scoped to the current run: loading research or invoking an active research tool
engages its contract, which remains required through that run even if compaction
drops the load record. A later greeting can use plain text. This is execution
policy, not a classifier for research intent or a guarantee about every factual
statement made outside a research invocation. Research still validates fresh
successful-fetch receipts and structured grounding before rendering its output.
See `jvagent/harness/skill_catalog.py:103` and
`jvagent/harness/native.py:238`.

Scheduled tasks against a catalog must explicitly name one configured skill.
That task uses the same native factory with that skill selected; it cannot
select an unlisted sibling or expand its tool permissions through pinned tools.
See `jvagent/action/native_harness/native_harness_action.py:300`.

The offline converter now maps finite `skills`/`skills_source` declarations into
this surface, preserving declared members and app-over-library overrides. This
is not a complete migration of the old router or stored state. Broader scripts,
wildcard declarations, automatic library search, legacy channel overrides and custom task metadata still need
their migration and compatibility evidence. Research-owned MCP evidence remains
refused until provenance is qualified; an unrelated read-only MCP skill may share
the same catalog. Historical graph upgrades and legacy deletion remain governed
by the [execution plan](../plans/2026-11-04-harness-rebuild-execution.md).

## Migrated shipped SOP names

The reusable retrieval skill is `library:pageindex-search-with-references` and
the reference app's public lookup skill is `web-lookup`. Existing underscore
IDs and paths need explicit declaration updates; no automatic alias or runtime
rewriting is installed. Copied/custom skill assets remain unchanged. See the
[asset migration and browser evidence](../plans/2026-11-05-native-skill-assets.md).
