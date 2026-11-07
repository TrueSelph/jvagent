# Native run and channel resource ceilings

The SDK owns enforcement through public `UsageLimits` and `Agent.run(timeout=...)`;
JV selects declared host ceilings before building/running the agent. The run-cost
ceiling also reaches official Harness spend policy, using the same value. Sources:
`jvagent/harness/native.py:635`,
`jvagent/action/native_harness/native_harness_action.py:496`.

An optional `NativeHarnessAction.context.channel_limits` map may **tighten** these
base Action fields: `max_requests`, `max_tool_calls`, `max_tokens`,
`max_output_tokens`, `timeout_seconds`, `max_run_cost_usd`, and the optional
`tool_timeout_seconds` execution policy. The latter is passed separately to SDK
tool definitions and qualified MCP adapters, not as a run argument. Sources:
`jvagent/harness/run_limits.py:79`, `jvagent/harness/native.py:321`. Example:

```yaml
max_requests: 20
timeout_seconds: 180
max_run_cost_usd: "2.00"
channel_limits:
  whatsapp_call:
    max_requests: 8
    timeout_seconds: 30
    max_run_cost_usd: "0.50"
```

`max_tokens` here counts aggregate run tokens; the per-response setting remains
`model_settings.max_tokens`. Token/cost checks follow SDK response accounting;
these are not provider-side reservations against in-flight overshoot.

Integers must be positive and correctly typed. Time must be positive/finite;
dollar values use positive finite decimal strings. An unset base output-token
ceiling can be tightened to a positive value. `null` cannot disable a channel
limit. Unknown fields, malformed declarations and any widening fail startup,
even if they belong to a different channel. Values are not echoed in errors.
Runtime revalidation covers changed persisted configuration
(`jvagent/harness/run_limits.py:48`).

Matching uses the exact admitted channel label; `whatsapp`, `whatsapp_call` and
`default` are separate keys. `default` is not a wildcard. An unmatched label uses
the base ceilings. Labels are not authentication or permission grants. A caller's
choice of label cannot raise the base allowance, but this map does not prove
transport/channel provenance. Conversation spending keeps its existing shared
ceiling and journal rather than resetting per channel. The selected scalar
values are copied into `Interaction.native_run.limits` and remain on success,
failure and cancellation (`native_harness_action.py:570`).

This map accepts no provider selection, tool surfacing, prompts, skills or
permissions. For execution deadline scopes, see
[tool deadlines](native-tool-deadlines.md). Keep procedures in skills. Legacy
`channel_overrides` are **not** automatically migrated: custom prompt policy,
per-tool cancellation/effect semantics and other controls still need explicit
qualification before retirement. In particular, an SDK run deadline does not
establish that an interrupted external effect did not happen. See
[qualification and browser evidence](../plans/2026-11-06-native-channel-limits.md).
