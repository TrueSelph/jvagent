# Native tool execution mode

Set `NativeHarnessAction.context.tool_execution_mode` to the public SDK mode:

```yaml
- action: jvagent/native_harness
  context:
    tool_execution_mode: sequential
```

The native default remains `parallel`. The SDK's public type currently admits
`parallel`, `sequential` and `parallel_ordered_events`. Validation uses that type,
not a JV copy of its enum. Unknown modes fail startup and fail before model work
when passed to the run helper. Sources:
`jvagent/harness/model_settings.py:7`,
`jvagent/action/native_harness/native_harness_action.py:121`.

`run_native_agent` scopes the library's public
`Agent.parallel_tool_call_execution_mode` around the run. The SDK schedules all
tool calls, including official capability tools and nested SubAgents. Its context
is restored after failures/cancellation and isolated between concurrent tasks.
There is no additional dispatcher or semaphore. Source:
`jvagent/harness/native.py:635`; regression:
`tests/harness/native/test_tool_execution_mode.py:1`.

Sequential mode allows a provider to return multiple tool calls in one response,
then executes them one at a time. It does not recreate legacy ticks or prevent
multiple calls from being emitted by the model. `parallel_ordered_events` orders
events while retaining parallel execution; it is not a concurrency limit.

The offline converter maps legacy `max_concurrent_tools: 1`, explicit `0` (the
legacy clamp to one) and the omitted legacy default to `sequential`. It records
the mapping and preserves source files. Numeric widths above one, negatives and
invalid types remain blockers: parallel mode cannot enforce an exact width.
Existing native configurations retain their parallel default. This setting does
not establish distributed capacity, provider rate limits, effect serialization
across runs or deployment qualification. Sources:
`jvagent/cli/harness_migrate.py:391`,
`jvagent/action/orchestrator/orchestrator_interact_action.py:4940`.

See [actual-model browser qualification](../plans/2026-11-06-native-tool-mode.md)
and [configuration migration](native-harness-migration.md).
