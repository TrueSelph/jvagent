# Native reasoning visibility

`NativeHarnessAction.publish_reasoning` defaults to false. Set it explicitly in
the execution Action's context to publish completed provider-supplied reasoning
text through the existing thought channel. This is a presentation policy: it
neither enables a model's reasoning nor changes its reasoning budget or prompt.
Models that emit no public ThinkingPart content produce no reasoning message.
Tool calls/results remain visible independently of this switch.

The adapter consumes public SDK PartEndEvent/ThinkingPart values, publishing each
completed part once. Starts, deltas, final-answer text, empty/signature-only parts
and opaque provider metadata are not projected as reasoning. The display is
bounded to 8,000 characters per part, with explicit truncation metadata; original
SDK history is not edited. The response bus supplies distinct segment IDs because
SDK part indices repeat across requests. Parent/child run identity remains in
event metadata. An interrupted part without an end event is not published as a
completed thought. Sources:
[`native_harness_action.py:29`](../../jvagent/action/native_harness/native_harness_action.py#L29),
[:179](../../jvagent/action/native_harness/native_harness_action.py#L179),
[:342](../../jvagent/action/native_harness/native_harness_action.py#L342).

Offline configuration migration maps a literal boolean `stream_reasoning_trace`
to `publish_reasoning`, preserving true/false. Non-boolean values block instead of
being coerced. This retires the old gear-specific emission path; native providers
and the SDK own the available content. Source:
[`harness_migrate.py:483`](../../jvagent/cli/harness_migrate.py#L483).

See [the acceptance record](../plans/2026-11-06-native-reasoning.md) for the
positive SDK event tests, actual GPT-4.1 browser scope and remaining provider/UI
qualification. No prompt is added to generate or simulate reasoning.
