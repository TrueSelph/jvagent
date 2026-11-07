# Native channel instructions

`NativeHarnessAction.channel_instructions` maps exact channel labels to literal
instruction text. The matching text is appended to the base `instructions` and
passed to the SDK's existing instructions interface. `default` is an ordinary
exact label, not a fallback. Unmatched labels receive only the base text. Sources:
`jvagent/harness/instructions.py:6`,
`jvagent/action/native_harness/native_harness_action.py:508`.

All declarations validate at startup and per turn, including unmatched profiles.
Malformed labels and nonliteral values fail without echoing instruction contents.
An empty profile string adds no instructions. Sources:
`jvagent/harness/instructions.py:14`,
`jvagent/action/native_harness/native_harness_action.py:226`.

Use this for channel presentation and host context. Keep domain procedures in
SKILL.md. This map selects no providers, tools, permissions, budgets or skill
ownership. A channel label grants no authority; instructions are model guidance,
not deterministic validation. Sources: `jvagent/harness/instructions.py:6`,
`jvagent/action/native_harness/native_harness_action.py:511`.

The offline migration command converts a qualified subset of legacy
`channel_overrides`; see [the migration contract](native-harness-migration.md).
Its remaining combined controls require explicit migration: domain SOPs into skills,
presentation into SDK instructions, ceilings into `channel_limits`, and remaining
tool/service policies into their qualified adapters. Adding this field does not
authorize dropping a legacy denial, deadline or response contract. Source:
`jvagent/cli/harness_migration_channels.py:24`.
