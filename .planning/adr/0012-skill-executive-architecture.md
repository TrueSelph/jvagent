# ADR 0012 — SkillExecutive architecture

**Status**: Accepted
**Date**: 2026-05-30
**Supersedes**: [`0010-executive-centers-architecture.md`](0010-executive-centers-architecture.md). The Executive + Centers split (a light Executive tick + recruited centers + a deterministic reflex + an IA center) is replaced by a single model-driven orchestrator with one tool surface. ADR-0011 (skills as judgment over capability) still holds.
**Relation to the walker-revisit mechanic**: unchanged — the orchestrator runs its whole turn inside one `execute()` call, no walker-revisit.

---

## 1. Context

ADR-0010 gave us a working brain-shaped agent, but it carried **dual routing**: a deterministic reflex *and* a model loop, plus three centers (Skills, IA, Persona) with their own verb sets, plus a separate Executive cognition tick. The IA center existed almost entirely to host one behavior — turn-lock — and the reflex existed to resume it deterministically. That is a lot of machinery for a single real use case (the signup interview).

Two observations collapse the design:

1. **Everything an agent can do is reachable as a tool.** With `Action.get_tools()`, action tools, anchored IAs (forwarding to `execute`), persona replies, core services, and skills are all just tools. Routing *is* tool selection. A separate IA center and capability registry are redundant.
2. **Turn-lock is just a flow that hasn't finished.** The only turn-locking action is the interview, and it already records a task when active. "Continue the locked flow" is "surface the active flow as a tool and let the model return to it until it's done" — a continuation, not a routing mode.

So we remove the reflex/IA-center/center-verb machinery and keep a single orchestrator that runs one model loop over the unified tool surface; when an active flow exists, the orchestrator surfaces that flow's tool and notes it so the model can choose to continue it.

### Decisions taken into this ADR (maintainer, 2026-05-30)

1. **Class**: `SkillExecutiveInteractAction` (weight `-200`, the sole orchestrator). Retire `ExecutiveInteractAction` and the `centers/` package.
2. **Persona**: `PersonaAction` implements `get_tools()` to furnish `reply` (thin publish) and `respond` (persona-framed) tools. No `PersonaCenter`.
3. **Manifest is the routing metadata**: an IA's `get_tools()` builds the tool *description* from its **manifest** (`purpose` + `activates_on` entry intents) via `routing_triggers()` — deliberately excluding any runtime-merged mid-flight anchors (e.g. an interview's cancel/update/confirm/skip/decline continuation intents), which describe in-flow behavior, not first-entry routing. This keeps the description clean and stops the relevance gate from over-matching. No separate deterministic anchor router.
4. **IAs stay IAs**: an anchored IA implements `get_tools()` to forward to `execute(visitor)`, with the `visitor` passed through from the SkillExecutive. No rewrite into a new base class.
5. **Egress is model-discretionary**: `reply`/`respond` are available to the model but actions furnish their own publish calls, so these tools are mostly for conversational banter. No hard exactly-once egress guard — only a light fallback when a turn ends with nothing emitted and no active flow.

---

## 2. Decision — one orchestrator, one tool surface, model-mediated continuation

```
                 ┌─────────────────────────────────────────┐
   user turn ──► │  SkillExecutiveInteractAction (-200)      │
                 │                                           │
                 │  1. active-flow surfacing (deterministic):│
                 │     active flow-task in TaskStore?         │
                 │       ├─ yes ─► make flow's tool visible + │
                 │       │           inject active_flow_note  │
                 │       └─ no  ─► (no flow context)          │
                 │                                           │
                 │  2. think-act-observe loop over the        │
                 │     unified tool surface (one model call   │
                 │     per tick, bounded). The model decides: │
                 │     continue the flow by selecting its     │
                 │     tool, or route elsewhere:              │
                 │       action tools · IA-as-tools ·         │
                 │       persona reply/respond · core tools · │
                 │       find_skill/use_skill · find/load_tool│
                 └─────────────────────────────────────────┘
```

Active-flow surfacing is a single deterministic step reading persisted state — **not** a parallel router and it does not force the flow to run. It makes the active flow's tool visible and notes it; the model decides whether to continue it or route the turn elsewhere.

### 2.1 Flow-continuation contract

A *flow* is any action that wants to span turns. It (a) records a control-task on the conversation `TaskStore` while active (the flow manages this itself), and (b) is continued by being selected as a tool again. The flow's **only** orchestrator-facing modification is being exposed via `get_tools()` (forwarding to `execute(visitor)`) — it gains no special resume entry point, no flow-control task-type hook, and no orchestrator-specific flags.

Continuation is model-mediated. Each turn the orchestrator detects the active flow with `continuation.active_flow_owner(visitor)` (the active task's `owner_action`, equal to the IA's tool name), makes that flow's tool visible, and injects the note from `continuation.active_flow_note(tool_name)` — roughly *"a multi-step flow is in progress; call `<tool>` to continue it if the user is engaging, otherwise handle their request normally — the flow stays active and resumes when the user returns."* The model then runs the normal loop:

- **Continue** — it selects the flow's tool, whose `get_tools` → `execute` loads and advances the flow's own session.
- **Route elsewhere** — for an off-topic utterance it picks a different tool; the flow is **not** forced to run. Its control-task persists and the flow resumes when the user returns.

This model-mediated routing is what prevents the user from being trapped in a flow and prevents the off-topic misroute (e.g. "Who is Eldon Marks?" during an active interview). There is no orchestrator-side answer/cancel/off-topic classifier; the model routes, and the flow's own session logic handles its steps. For the interview, continuation is just its existing `execute(visitor)` reached through its tool.

### 2.2 Tool surface + progressive disclosure

The tool surface is the union of every enabled action's `get_tools()` (including IA-as-tools and persona tools), the core-tool set, and the skill meta-tools. A **tool catalog** (mirroring the skills catalog) exposes `find_tool`/`load_tool` so the prompt carries a slim index rather than every schema — bounding prompt size as the surface grows.

### 2.3 Manifest as the routing signal

An IA's `get_tools()` builds the returned tool's description from its **manifest**
— `purpose` plus `activates_on` (the entry intents), surfaced via
`InteractAction.routing_triggers()` — so the model selects it on intent without a
separate anchor router. `routing_triggers()` uses `manifest.activates_on` and
falls back to static `anchors` only when no manifest is declared; it never
includes runtime-merged continuation anchors (cancel/update/confirm/skip/decline),
which describe in-flow behavior and would otherwise bloat the description and make
the orchestrator's relevance gate match unrelated turns. The same
`routing_triggers()` feed the SkillExecutive's visibility gate (a flow's tool is
in the prompt only when active or when the utterance is trigger-relevant).
First-entry and continuation are both model-judged.

---

## 3. Invariants

1. **One model call per tick**, loop-enforced via `ModelBudget`; the loop is bounded by an activation budget. *(Implementation note: shipped without a dedicated `ModelBudget` class — the loop makes exactly one `_run_model` call per tick and is bounded by the `activation_budget` counter.)*
2. **Flow continuation is model-mediated.** Active-flow surfacing reads persisted state deterministically (no model) but never forces a flow to run — it makes the flow's tool visible and notes it; the model decides whether to continue it or route elsewhere. *(Superseded by [ADR-0013](0013-togglable-deterministic-turn-lock.md): continuation mode is now configurable via `lock_active_flow`; this model-mediated behavior applies when `lock_active_flow=False`.)*
3. **Turn-lock is emergent.** A flow's control-task persists on the conversation `TaskStore`; the flow resumes when the model selects its tool. There is no orchestrator-side answer/cancel/detour branch — routing is the model's, and the flow's own session logic handles its steps. *(Superseded by [ADR-0013](0013-togglable-deterministic-turn-lock.md): when `lock_active_flow=True` (default), the orchestrator deterministically routes the turn to the active flow's IA.)*
4. **Routing is tool selection.** There is no separate router or capability registry; IAs, persona, core services, and skills are all tools.
5. **Actions own their output.** Actions publish their own results; `reply`/`respond` persona tools are model-discretionary. A turn that ends with no emission and no active flow gets a single fallback reply.
6. **Access control gates tool dispatch** (`tool:*`), including IA-as-tool execution (`tool:delegate:{name}` preserved).
7. **Walk-path curation.** The SkillExecutive coexists with the interact pipeline, so a routable IA is still a top-level `InteractAction` that the walker would otherwise execute every turn. Each turn the orchestrator curates the remaining walk path (`visitor.curate_walk_path`) to **drop tool-exposed (routable) IAs** — they are reached only by the model selecting their tool — while keeping itself, `always_execute` IAs, and non-routable IAs. Without this, an anchored IA self-executes on every interaction regardless of routing.

---

## 4. Consequences

**Removed**: the reflex, the IA center, `sustained.py` turn-lock resume, the `ACTIVATE/RESPOND/YIELD` + frame-stack center machinery, the earlier deterministic continuation contract (its outcome enum, its resumable-flow protocol, and the per-IA resume entry point and flow-control task-type hook), `BaseCenter`/`PersonaCenter`/`SkillsCenter`, the capability registry's routing role, and the `can_interrupt` manifest *branching* (interruptibility is now automatic — an off-topic message is simply routed to a different tool by the model, not gated by an orchestrator flag).

**Gained**: a single mental model (one loop, with the active flow surfaced as a tool), a uniform tool surface, and turn-lock as an emergent, model-mediated flow property.

**Risks**: (a) first-entry routing accuracy now depends on model tool-selection — mitigated by anchors-in-description + a routing nudge + tests; (b) trivial-turn latency, since every non-flow turn enters the loop — mitigated by the slim tool catalog and a `converse` fast-reply skill; both measured at rollout.

---

## 5. Alternatives considered

- **Keep the deterministic reflex as a thin pre-gate in front of the loop** (the "sticky-tool gate"). Rejected in favor of the model-mediated flow-continuation model: it folds the same guarantee into the unified tool surface as one mechanism instead of a second routing path, which was the maintainer's explicit goal ("do away with dual routing").
- **A deterministic continuation contract that re-runs the active flow each turn and reads an outcome enum (ACTIVE/COMPLETE/YIELD) from a dedicated resume entry point on the flow.** Rejected — it adds an orchestrator-specific contract to every turn-spanning IA and a second routing path; surfacing the active flow as a tool and letting the model route achieves the same continuation with no IA changes beyond `get_tools()`.
- **A general turn-lock subsystem keyed off `manifest.turn_lock`.** Rejected as over-built: turn-lock is empirically only the interview; surfacing the active flow as a tool covers it and generalizes to any future turn-spanning flow with zero new plumbing.
