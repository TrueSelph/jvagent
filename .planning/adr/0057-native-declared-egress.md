# ADR-0057 — SDK-owned output and declared native egress policy

- Status: Accepted for the qualified native profile; broader parameter migration remains open
- Date: 2026-11-06
- Refines: [ADR-0037](0037-parameters-and-directives-as-the-only-customization-surfaces.md) and [ADR-0038](0038-one-egress-gate-for-every-transport.md) for native execution
- Governing direction: [harness rebuild](../plans/2026-11-04-harness-rebuild-execution.md)

## Context

An actual GPT-4.1 browser request produced a correct unavailable-tool answer,
but ReplyAction/ResponseBus inherited an implicit legacy disclosure rule and
replaced it with a generic deflection. The SDK result and delivered response
disagreed semantically. The original failure is retained in
[the qualification record](../plans/2026-11-06-native-controls.md).

## Decision

Pydantic AI owns native generation and model lifecycle. SDK-owned output reaches
literal publication with declared interaction response policy and existing channel
delivery. It does not inherit implicit legacy identity/tool-name/style defaults
or require a second compose model. This concerns content policy, not authenticated
tool access, host identity, spending, research evidence or delivery acknowledgement.

Both egress routes use one selector. Native ownership requires the current run's
unique persisted registration and matching host interaction/conversation/user/
session bindings; request/message metadata is insufficient. Explicit scrub rules
retain their existing egress gate. Legacy output retains its current policy until
migration/removal. Sources: `jvagent/action/response/policy.py:8`, `:48`.

## Consequences and limits

The direct, bus and streaming routes share policy resolution. Native interfaces
can accurately report requested tool availability rather than hiding it behind
an implicit deflection. Existing historical responses remain unchanged.

Only the demonstrated explicit interaction scrub contract is qualified here.
Broader Action/skill prompt/guard projection requires SDK-owned adapters and its
own acceptance evidence. Remove the legacy fallback and owner distinction after
retained-state migration and old execution deletion. This decision does not
qualify the enterprise release or retire the existing universal delivery gate.
