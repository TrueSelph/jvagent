# ADR 0040 — Planning and sticky finalize on the heavy gear

**Status**: Superseded (config surface) by [ADR-0041](0041-gearing-and-cost-policy-in-core.md); policy retained in core
**Date**: 2026-07-27
**Relation**: Extends [ADR-0016](0016-model-gearing-light-heavy.md) and
[ADR-0039](0039-gearing-escalate-after-first-tool.md). Framework-facing gearing
knobs — not consumer-specific heuristics.

---

## 1. Context

ADR-0039 escalates after the first substantive tool. Multi-step **plans** are
decided on tick 0 via `update_plan` before any substantive tool runs; light
gear still skipped planning. Budget/duration **partial-compose finalize** was
hardcoded to light, so a turn that gathered on heavy could wrap up on light and
drop structure.

## 2. Decision

1. **`escalate_on_planning` (default true).** When `planning: true` on the
   orchestrator, `_select_gear` returns heavy for the whole turn (including
   tick 0). No-op when planning is off. Agents may set false to keep a light
   first tick with planning enabled.
2. **`sticky_finalize_gear` (default true).** Partial-compose finalize uses the
   gear the turn already selected (`last_gear`), not a forced light wrap-up.
   Disable to restore ADR-0016’s light finalize.

No utterance classifiers, domain pins, or example-app special cases.

## 3. Consequences

- Planning-on agents reason about `update_plan` on heavy from the first tick.
- Finalize quality tracks the turn’s gear; cost rises slightly vs forced light.
- Reply-only / planning-off agents unchanged.

## 4. Alternatives considered

- Heuristic “multi-step utterance” router onto heavy — rejected (thin harness;
  imprecise; consumer-specific).
- Always-heavy when any skill is in the catalog — too broad; would kill light
  gear for every skilled agent.
