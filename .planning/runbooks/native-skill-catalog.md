# Native skill catalog

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
Directory/name mismatches, duplicate names, unsupported metadata and unavailable
Action/tool/channel prerequisites fail admission. Every configured member must
also pass official loader preflight before a model request is made.
See `jvagent/harness/skill_catalog.py:24` and
`jvagent/harness/skill_assets.py:34`.

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
is not a complete migration of the old router or stored state. Scripts, wildcard declarations, automatic library
search, channel-specific catalog composition and custom task metadata still need
their migration and compatibility evidence. Research-owned MCP evidence remains
refused until provenance is qualified; an unrelated read-only MCP skill may share
the same catalog. Historical graph upgrades and legacy deletion remain governed
by the [execution plan](../plans/2026-11-04-harness-rebuild-execution.md).
