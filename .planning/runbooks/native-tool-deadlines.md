# Native Action tool deadlines

Declare a positive finite `timeout_seconds` on a JV `Tool`, or publish an Action
method with `@tool(timeout_seconds=15)`. Omit it or use `None` for no per-tool
deadline. Booleans, strings, nonpositive values and nonfinite numbers are rejected;
the native factory revalidates mutable metadata at admission.
Sources: `jvagent/tooling/tool.py:9`, `jvagent/tooling/tool_decorator.py:84`,
`jvagent/harness/native.py:113`.

The native adapter assigns the public SDK `Tool.timeout` field. Pydantic AI owns
the timer and tool dispatch. The decorator does not wrap direct Python calls.
Blocking or cancellation-resistant Python code has no hard-preemption guarantee.
Source: `jvagent/harness/native.py:128`.

An optional positive finite `NativeHarnessAction.tool_timeout_seconds` supplies
a default execution deadline. Exact-channel `channel_limits` may tighten it;
they cannot disable or widen an active base deadline. The minimum of an Action's
own deadline and host policy applies. Public SDK tool definitions carry the
policy for function tools, including foreground delegation and script execution;
children inherit it. Sources: `jvagent/harness/run_limits.py:52`,
`jvagent/harness/native.py:321`, `jvagent/harness/native.py:634`.

Reviewed MCP adapters use the public FastMCP client RPC timeout, preserving a
tighter service timeout. MCP initialization keeps its separate `init_timeout`.
Selected host policy requires a qualified toolset deadline adapter; an unknown
toolset cannot merely ignore it. Sources:
`jvagent/action/mcp/mcp_action.py:960`, `jvagent/harness/native.py:76`.

These are SDK function-body and MCP RPC deadlines, not an end-to-end legacy
wrapper clock. SDK capability-loading and output controls remain bounded by the
public run timeout; loading is not assigned a fabricated function-tool timeout.
The [clock qualification](../plans/2026-11-07-native-sdk-tool-policy.md) records
these distinct scopes and the offline legacy `tool_call_timeout` mapping.

A timeout is not evidence of rollback. For an annotated mutation whose execution
has begun, SDK retry/failure controls become `MutationOutcomeUnknown`, terminating
the run before another model attempt or queued mutation. The persisted effect
retains its uncertainty and subsequent turns require reconciliation. Reads retain
the pinned upstream terminal-receipt repair. Remove that repair when upstream
terminalizes retry receipts; retain the mutation policy unless upstream provides
equivalent non-retriable effect semantics. Sources:
`jvagent/harness/read_retries.py:38`, `tests/harness/native/test_tool_deadlines.py:32`.

A reviewed native mutation whose receipt still has exactly `effect_summary=None`
has not crossed its persisted intent fence. SDK failure can terminalize that
receipt without inferring rollback. Annotated writes remain uncertain and stop
the run. Mutation identity/recovery-scope methods must be read-only before that
fence. Sources: `jvagent/harness/read_retries.py:64`,
`jvagent/harness/mutation_tools.py:52`.

jvchat displays typed SDK failure results as failed tools. A finished call without
an acknowledgement displays an unconfirmed tool warning; it does not manufacture
a result or infer whether an effect happened. Sources:
`jvagent/action/native_harness/native_harness_action.py:74`,
`jvchat/src/lib/toolStatus.ts:4`.

Qualification and remaining limitations belong in the
[milestone record](../plans/2026-11-06-native-tool-deadlines.md).
