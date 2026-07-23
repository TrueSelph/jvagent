# ADR-0035: Orchestrator Configuration Surface Audit

## Status

**Proposed** — Not yet accepted or implemented.

## Context

The Orchestrator's configuration surface has grown organically to ~64 attribute knobs across multiple concerns:
- Model gearing and timeout controls
- Tool surface and skill activation
- Planning, continuation, and turn-lock behaviors
- Streaming and observability
- Channel-specific overrides

ADR-0015 documented the initial configuration surface. As Wave 6 monolith splits proceed, we need to audit the current state and determine:
1. Which knobs are essential vs. vestigial
2. Whether the grouping reflects current usage patterns
3. What the right long-term organization should be

## Decision

**Deferred.** This ADR records the intent to conduct a comprehensive audit of the 64 attribute knobs, but does not yet propose the outcome. The audit will:
- Inventory all current attributes and their usage
- Identify deprecated or redundant controls
- Propose a consolidated, well-documented configuration schema
- Update or supersede ADR-0015 with the findings

## Consequences

- Wave 6 extractions proceed without awaiting the audit
- The audit will be conducted in a follow-up phase
- ADR-0015 remains the current reference until superseded

## References

- ADR-0015: Orchestrator configuration surface (initial)
- Wave 6 monolith splits (in progress)
