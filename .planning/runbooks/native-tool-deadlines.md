# Native Action tool deadlines

Declare a positive finite `timeout_seconds` on a JV `Tool`, or publish an Action
method with `@tool(timeout_seconds=15)`. Omit it or use `None` for no per-tool
deadline. Booleans, strings, nonpositive values and nonfinite numbers are rejected;
the native factory revalidates mutable metadata at admission.
Sources: `jvagent/tooling/tool.py:9`, `jvagent/tooling/tool_decorator.py:84`,
`jvagent/harness/native.py:113`.

The native adapter assigns the public SDK `Tool.timeout` field. Pydantic AI owns
the timer and tool dispatch. The decorator does not wrap direct Python calls,
and this does not migrate legacy global/channel timeouts or the separate MCP,
script and delegation deadline policies. Blocking or cancellation-resistant Python
code has no hard-preemption guarantee. Source: `jvagent/harness/native.py:216`.

A timeout is not evidence of rollback. For an annotated mutation whose execution
has begun, SDK retry/failure controls become `MutationOutcomeUnknown`, terminating
the run before another model attempt or queued mutation. The persisted effect
retains its uncertainty and subsequent turns require reconciliation. Reads retain
the pinned upstream terminal-receipt repair. Remove that repair when upstream
terminalizes retry receipts; retain the mutation policy unless upstream provides
equivalent non-retriable effect semantics. Sources:
`jvagent/harness/read_retries.py:38`, `tests/harness/native/test_tool_deadlines.py:32`.

jvchat displays typed SDK failure results as failed tools. A finished call without
an acknowledgement displays an unconfirmed tool warning; it does not manufacture
a result or infer whether an effect happened. Sources:
`jvagent/action/native_harness/native_harness_action.py:74`,
`jvchat/src/lib/toolStatus.ts:4`.

Qualification and remaining limitations belong in the
[milestone record](../plans/2026-11-06-native-tool-deadlines.md).
