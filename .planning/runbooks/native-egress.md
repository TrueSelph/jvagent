# Native response policy

SDK-owned replies use their declared interaction response rules. They do not
implicitly inherit the old framework's identity/tool-name and style scrubs.
The central selector is shared by ReplyAction's direct path and ResponseBus,
including streaming. Sources: `jvagent/action/response/policy.py:48`,
`jvagent/action/reply/reply_action.py:38`,
`jvagent/action/response/response_bus.py:20`.

Native ownership requires one persisted registration for the current run, a
registration identity, an agent binding, matching SDK conversation/session and
matching host interaction, conversation, user and session metadata. A bare
`native_run` marker or request/message metadata does not select this policy.
Missing, duplicate or foreign registration bindings retain the transitional
legacy policy. Source: `jvagent/action/response/policy.py:8`.

An explicit interaction scrub rule still applies through the existing egress
gate. Malformed native policy containers refuse publication. Authenticated tool
access, host context, budgets, research output contracts and delivery
acknowledgements retain their own enforcement boundaries; this selector only
chooses response rules. Broader Action/skill prompt/guard parameter projection
into native execution still needs qualification. Do not infer that every legacy
parameter contract has migrated from the tested explicit scrub case.

The legacy fallback and ownership distinction are temporary compatibility code.
Remove them after retained-state migration and old execution removal, leaving
one SDK-owned execution path and declared JV response policy. Existing historical
responses are not rewritten. See the [browser qualification](../plans/2026-11-06-native-controls.md)
for the original failed deflection, the corrected reply and preserved records.
