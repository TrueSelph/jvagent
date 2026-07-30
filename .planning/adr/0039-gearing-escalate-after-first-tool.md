# ADR 0039 — Gearing escalates after the first substantive tool

**Status**: Superseded (config surface) by [ADR-0041](0041-gearing-and-cost-policy-in-core.md); policy retained in core
**Date**: 2026-07-26
**Supersedes**: [ADR-0016](0016-model-gearing-light-heavy.md) §2.2 default for `escalate_after_tool_calls` only (profiles, sticky escalation, and `escalate_on_skill` unchanged).

---

## 1. Context

ADR-0016 defaulted `escalate_after_tool_calls` to **2** so single-tool→reply stayed on the light gear. In practice a common multi-step shape is **one substantive tool then a deliberative follow-up** (e.g. `pageindex__assimilate` → decide whether to fetch more / write a report / reply). Egress tools (`reply`/`respond`) do not count as substantive, so that turn never reached the threshold of 2 and the heavy model never engaged — weak shortcuts on capture/report/assimilate style asks.

Skill activation already escalates via `escalate_on_skill` (default true). The gap is multi-tool / tool→decide without a skill lock.

## 2. Decision

Change the default `escalate_after_tool_calls` from **2** to **1**.

- Tick 0 (no substantive tools yet): **light** (reply-only and first tool pick stay cheap).
- After ≥1 substantive tool call: subsequent ticks are **heavy** (sticky).
- Any active skill (`escalate_on_skill`): **heavy** immediately (unchanged).

Agents that want the old stickier light path set `escalate_after_tool_calls: 2` explicitly.

> **Superseded (2026-07-27):** [ADR-0041](0041-gearing-and-cost-policy-in-core.md)
> hard-codes threshold `1` and removes the attribute — no YAML escape hatch.

## 3. Consequences

- Multi-tool and tool→decide turns pay the heavy model on the follow-up — closer to the intent of ADR-0016 §1.
- Single-tool→reply: the final tick after the tool is heavy (slightly more cost than ADR-0016’s original default; accepted).
- Reply-only turns remain light.

## 4. Alternatives considered

- Count `reply`/`respond` toward the threshold — couples gearing to egress naming; rejected.
- Upfront utterance router onto heavy — rejected in ADR-0016; still rejected.
