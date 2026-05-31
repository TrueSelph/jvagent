# ADR 0018 — Lean tool surfacing (progressive tool disclosure)

**Status**: Accepted
**Date**: 2026-05-31
**Relation**: Refines [ADR-0012](0012-skill-executive-architecture.md) (the unified tool surface + `find_tool`/`load_tool` catalog). Complements [ADR-0017](0017-two-skill-specs-code-execution-substrate.md) (which can add many tools — file_interface, code_execution, an MCP filesystem server).

---

## 1. Context

The orchestrator builds one unified tool surface and renders it into the system
prompt every think-act-observe tick. A `find_tool`/`load_tool` catalog already
exists to keep the prompt slim — dispatch resolves against the *full* surface,
so a tool need not be listed to be callable, and `block_raw_tool_invocation`
already steers the model through the catalog for hidden tools.

But the catalog was **decorative**: `_assemble_tools` marked essentially every
plain action tool *and* every MCP tool `visible`, so the prompt dumped the whole
surface (~40 tools once a filesystem MCP, `file_interface`, `pageindex`,
`skill_hub`, `code_execution`, etc. are enabled). Nothing was ever hidden, so
`find_tool` never mattered and every tick paid for the full list.

The orchestrator is meant to be a lean, performant harness, so the fix must cut
the prompt **without** trading it for a discovery round-trip on every turn.

## 2. Decision

Change only the **default visibility policy**; keep the full surface, dispatch,
and the catalog exactly as they are.

### 2.1 Always-visible core (unchanged)

Egress (`reply`/`respond`), the meta-tools (`find_tool`/`load_tool`/`find_skill`/
`use_skill`), core tools (`get_current_datetime`, …), and an active-flow IA tool
(already relevance-gated) are always listed.

### 2.2 The long tail is hideable

Plain action tools and MCP tools — the bloat — are collected into a `longtail`
set during assembly and surfaced per a policy applied **once per turn**:

- **Below the threshold** (`len(longtail) ≤ lean_tool_threshold`, default 15):
  list every tool (today's behaviour — small agents are unchanged).
- **Above it**: list only the **relevance pre-surfaced** few plus any tool the
  user named; the rest stay on the full surface, reachable via `find_tool`.
  `lean_tool_threshold = 0` disables lean entirely.

### 2.3 Relevance pre-surface (the efficiency lever)

`_presurface_tools(utterance, longtail, k=lean_presurface_k)` ranks the long tail
by cheap token overlap of the user's significant words against each tool's
`name`+`description` (reusing the `_anchor_relevant` tokenizer — no model call),
and pre-surfaces the top `k` (default 6) with non-zero overlap. So "send an
email" pre-surfaces `*__send_email` with **zero** discovery ticks, while novel
needs cost one `find_tool` call. It runs once per turn in `_assemble_tools`, so
it adds no per-tick cost.

### 2.4 Supporting changes

- When lean is active the tools section appends a one-line hint that the list is
  partial and `find_tool(query)` reveals the rest (so the model knows to look).
- `find_tool` output is grouped by `<namespace>__` prefix, so one call reveals a
  whole integration compactly.

## 3. Consequences

- **Smaller prompt × every tick.** The dump collapses from ~40 to ~8–12 entries;
  on multi-tick agentic tasks (where the harness spends the most) this is a net
  token win even after the rare discovery tick.
- **`find_tool`/`find_skill` become genuine** — the catalog now does real work.
- **Self-balancing, opt-out.** Threshold-auto means tiny agents keep the simple
  full list with zero behaviour change; large agents auto-slim. Both knobs
  (`lean_tool_threshold`, `lean_presurface_k`) are configurable per agent.
- **No new failure modes.** Dispatch already resolves against the full surface
  and `block_raw_tool_invocation` already supports hidden tools — only what the
  prompt *lists* changed.

## 4. Alternatives considered

- **Hide everything, no pre-surface ("essentials-only")** — leanest prompt, but
  a discovery round-trip on nearly every task and harder on weaker models;
  rejected *as the default*. It remains **reachable by config** for the cases
  where it wins (very large surfaces, strong models where prompt cost dominates):
  set `lean_presurface_k: 0` (optionally `lean_tool_threshold: 1`). Skills stay
  listed regardless — they're few and carry the "prefer a whole SOP" signal.
- **Always lean** — changes behaviour for tiny agents that fit fine; rejected in
  favour of the threshold.
- **Collapse integrations into namespace summaries in the prompt** — kept as the
  `find_tool` grouping, not as the primary prompt mechanism (the relevance
  pre-surface is the better latency/size trade).
