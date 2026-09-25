# ADR 0055 — Unified capability discovery (`find_capability`)

**Status**: Accepted
**Date**: 2026-09-24
**Relation**: Extends [ADR-0018](0018-lean-tool-surfacing.md) (lean progressive disclosure) and [ADR-0012](0012-skill-executive-architecture.md) (skills-first executive). Composes with [ADR-0043](0043-skill-only-tools.md) (skill-gate annotations on tool hits). Does not supersede them.

---

## 1. Context

Lean surfacing (ADR-0018) keeps the prompt slim: most tools are hidden and
reached via discovery meta-tools. Discovery was split into two doors:

- `find_tool` — tools only
- `find_skill` / skills-first prompt — skills

Observed failure: under lean + `block_raw_tool_invocation`, models default to
`find_tool` (the loop protocol said the list may be PARTIAL → call find_tool).
Domain SOPs never activate; the model thrashes `find_tool` / `load_tool` and
never calls the owning skill's propose tools. Hosts (e.g. Integral) compensated
with per-domain host directives and `pinned_tools` — unsustainable.

Skills were already listed in AVAILABLE SKILLS; the miss was **which discovery
tool the model chose**, not whether skills existed.

## 2. Decision

Add **`find_capability(query)`** as the **primary** discovery meta-tool:

1. Rank **skills** (name + description + tags) with the same token-overlap
   rules as tools.
2. Rank **tools** (excluding meta/egress noise).
3. Render **Skills first**, then **Tools**, with explicit next-step cues:
   `use_skill("…")` / `load_tool("…")`.
4. When any skill matches, lead with: prefer `use_skill` before ad-hoc tools.
5. When a tool hit is skill-gated (ADR-0043) and the gate is closed, keep
   `(via skill: …)` and prefer `use_skill` over bare `load_tool`.

**Compat:** `find_tool` and `find_skill` remain callable thin aliases. Their
descriptions point at `find_capability`. Prompt / lean / bounce copy steers
to `find_capability` first.

**Unchanged:** `use_skill`, `load_tool`, lean threshold, pins, `denied_tools`,
skill-only gate semantics.

## 3. Consequences

- One discovery door for lean-hidden capabilities; skills-first becomes
  mechanically reinforced when the model follows the lean hint.
- Host-level “pin every write tool” / “inject use_skill for domain X”
  workarounds can be retired once agents pin a jvagent that includes this ADR.
- Prompt size grows by one always-visible meta-tool schema (same class as
  find_tool).
- Empty dual-section searches keep the existing no-match recovery (one keyword
  retry / say cannot / do not repeat) so the repeat-guard does not eat the turn.

## 4. Implementation pointers

- [`jvagent/action/orchestrator/catalog.py`](../../jvagent/action/orchestrator/catalog.py)
  — `build_capability_catalog_tools`, `_rank_skills`
- Assembled in `_assemble_tools` after `build_catalog_tools`
- Protected via `STEER_EXEMPT` like other catalog meta-tools
