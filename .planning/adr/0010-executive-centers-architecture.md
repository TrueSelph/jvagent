# ADR 0010 — Executive + Centers architecture

**Status**: Superseded by [`0012-skill-executive-architecture.md`](0012-skill-executive-architecture.md). The Executive + Centers split (Executive tick + recruited centers + deterministic reflex + IA center) was replaced by a single model-driven orchestrator over one tool surface; the body below is retained as historical record (do not edit).
**Date**: 2026-05-28
**Supersedes (in spirit)**: the earlier peer-orchestrator and router-elimination ADRs, both since removed. Those patterns have been removed from the codebase; the Executive is the surviving orchestrator pattern alongside Rails.
**Relation to the walker-revisit mechanic**: This pattern does **not** use walker-revisit. The Executive owns its own control loop inside a single `execute()` call.

---

## 1. Context

The prior orchestrator pattern (since removed) used a peer topology in which N components could hand off to each other freely. That one decision was the root of friction that successive patches never fully resolved: routing had no single authority, one component became a god-object owning both skills *and* IA delegation, IA dispatch was scattered across many homes with no source of truth, and a stack of band-aids existed only to contain the resulting looping. Delivery was welded onto the response verb through an over-patched egress path.

We want a **brain-shaped** agent: a small set of specialized *centers*, each doing one thing well, recruited by one *executive* that knows them all and integrates their work into a coherent reply — with the deterministic rails feel jvagent was built on.

### Decisions taken into this ADR (maintainer, 2026-05-28)

1. **Blast radius**: greenfield, substrate included.
2. **Topology**: one executive orchestrator; centers are leaves it activates. No center→center edges.
3. **Delivery**: a single Persona center is the universal egress; per-activation choice of integrate-vs-voice-directly.
4. **Substrate**: a fresh control loop, not walker-revisit; per-tick guarantees re-derived at loop level.
5. **Capabilities**: one unified, tiered capability registry (skills + IAs + anchors).
6. **Rollout**: ship alongside Rails as a peer orchestrator pattern.

---

## 2. Decision — a neurological model

A single `ExecutiveInteractAction` (weight `-200`, the sole orchestrator at that slot) is the agent's **central executive** (prefrontal cortex). It engages light conversation itself, holds working memory, and is the *only* component that activates centers. Centers are specialist leaves. Persona is the sole egress. A deterministic reflex path bypasses the executive for hardened pathways.

```
Reflex (no model): anchor hit / open IA session?  ──► activate that center directly
        │ miss
        ▼
   ┌─────────────┐   ACTIVATE(center, brief, on_done)   ┌──────────────┐
   │  EXECUTIVE  │ ─────────────────────────────────────►│   CENTERS    │
   │  (PFC,      │ ◄───────────── RETURN(result) ────────│  (leaves)    │
   │  light LM)  │        (lands in working memory)       ├──────────────┤
   │             │                                        │ Skills       │
   │ working mem │   RESPOND(content) ──► Persona center ─┤ IA (rails)   │
   └─────────────┘                       (voices, ends)   │ Persona      │
                                                          └──────────────┘
```

### 2.1 Anatomy

| Part | Brain analogue | Role | Model |
|---|---|---|---|
| **Executive** | prefrontal cortex / central executive | engages trivial conversation; knows all centers; holds working memory; activates centers; integrates; decides to respond | light |
| **Skills center** | association cortex | skill-based reasoning (think-act-observe over the skill surface) | heavy |
| **IA center** | reflex/procedural pathways | hardened, anchored, rails interact-actions; sustained activation = turn-lock | usually none |
| **Persona center** | Broca's area | language/identity; the sole producer of user-facing prose | light |
| **Reflex** | spinal reflex arc | deterministic, no-model routing (anchors, open sessions) that bypasses the Executive | none |
| **Working memory** | PFC working memory | per-turn buffer of center results; carries sustained IA activation across turns | — |
| **Control loop** | attention / cognitive effort | the mechanism; bounded by an activation budget | — |

The registry is the executive's **map of its own centers** — a tiered `CapabilityRegistry` (skills + IAs + anchors). The Executive reads a token-bounded routing view; the Skills/IA centers read their execution views. (This is the survivor of Wave-6's shared-registry idea, made the substrate.)

### 2.2 Verbs — role-typed, minimal

The Executive and the centers speak different, tiny vocabularies. There is no shared 5-verb soup.

```python
# Executive emits one of:
ACTIVATE(center: str, brief: Brief, on_done: Literal["integrate", "voice"] = "integrate")
RESPOND(content)        # hand to the Persona center to voice, then end the turn
YIELD()                 # cede to the rails weight chain

# A center emits one of:
STEP(scratch=None)      # "I did internal work (tools/IA); recruit me again"
RETURN(result)          # deposit result in working memory; control returns to the Executive
```

`on_done="voice"` routes a center's `RETURN` straight to the Persona center and closes the turn; `on_done="integrate"` (default) lands it in working memory so the Executive can frame it or activate another center. `RESPOND` is the Executive activating the Persona center as the closing act.

### 2.3 Control loop

```
ExecutiveInteractAction.execute(walker)                 # ONE walker visit = whole turn
  wm = rehydrate_working_memory(conversation)           # sustained activations (turn-lock)
  if reflex := deterministic_reflex(utterance, registry):   # no LM
      activate(reflex.center, reflex.brief); ...        # hardened path, may skip executive
  budget = activation_budget
  while budget and not turn_done:
      gate_access(current)                              # per-tick AC  (re-derived)
      with model_budget(max_calls=1):                   # one call/tick (re-derived)
          act = await current.tick(ctx, wm)             # Executive or a center
      record_tick(current, act); flush_stream()         # trace + streaming (re-derived)
      apply(act, wm):                                   # ACTIVATE→recruit; STEP→same;
          ...                                           # RETURN→wm + back to Executive;
          RESPOND→persona voices, end; YIELD→break
      budget -= 1
  persist_working_memory(conversation)                  # sustained activation across turns
  # loop ended → execute() returns → walker continues weight chain (rails coexistence)
```

Only the Executive activates centers, so the recruitment graph is a star with depth 1: no cycles, no ping-pong, no shift budget. The single `activation_budget` is the only bound, sitting beneath jvspatial's `max_visits_per_node`.

### 2.4 Pipeline citizenship (amended 2026-05-29 after live smoke)

The Executive is a well-behaved `InteractAction` at weight `-200`. The interact-pipeline convention holds for **`always_execute` IAs**: they run before and after the Executive as ordinary weight-chain members — pre-Executive (weight `< -200`; e.g. auth, channel normalization, first-turn intro) and post-Executive (weight `> -200`; e.g. audit, analytics, logging).

**But routable IAs must be curated out.** The original draft said the Executive does *not* curate the walker queue. Live smoke disproved this: with no `InteractRouter` gating top-level IAs, an anchored, non-`always_execute` IA (a turn-locking signup interview) self-executed at weight `-40` *every turn*, in parallel with the Executive — bypassing and duplicating the IA center, and creating a stray turn-lock. So the Executive **curates the remaining walker queue to `{self} ∪ always_execute IAs`** (`_curate_walker_queue`). Routable IAs (anchored / non-`always_execute`) are owned by the IA center and reached only through it; they never self-run. "Pipeline citizenship" therefore means: `always_execute` cross-cutting IAs run as substrate citizens; everything routable is the IA center's domain.

---

## 3. Re-derived guarantees (we left walker-revisit)

| Property | Walker-revisit gave it via | Executive re-derives via |
|---|---|---|
| One model call per step | one `execute()` per visit | `model_budget(max_calls=1)` around each tick; exceeding aborts the tick |
| Per-step access control | `enforce_interact_action_access()` | `gate_access()` per tick + AC on each ACTIVATE and each rails IA run |
| Per-step observability | walker on-visit hooks | `record_tick()` appends an activation-trace event per tick |
| Streaming flush | between visits | explicit `flush_stream()` after each tick |
| Runaway protection | `max_visits_per_node` + `max_iterations` | one `activation_budget` on the loop |
| Rails coexistence | walker continues weight chain | loop ends / `YIELD` → `execute()` returns → walker proceeds |

SPEC impact: a new **§3.5 "Executive scheduler pattern"**; §3.3/§3.4 and invariants 9–13 are untouched. §11 inv. 4 is honored — the Executive does not recursively invoke the walker; it loops internally and returns once.

### Executive invariants (proposed SPEC §3.5)

1. **One model call per tick**, loop-enforced.
2. **Only the Executive activates centers.** Centers are leaves: they `STEP` or `RETURN`. No center→center recruitment → no cycles; bounded by `activation_budget`.
3. **Working memory is the per-turn state**; centers hold no per-turn instance/class state. *Sustained activation* (turn-lock) is persisted on the conversation `TaskStore` (an `executive_sustained` task, or the rails IA's own active task), not carried in working memory — amended 2026-05-29, superseding the original `conversation.context["executive_suspended"]` key. See [`adr/0011`](0011-skills-two-kinds.md) sibling note and `jvagent/action/executive/sustained.py`.
4. **The Persona center is the only path to final user-facing prose** — the Executive included; centers never publish final prose (transient acks excepted).
5. **The reflex path is deterministic** (no model). Model calls come only from the Executive, the Skills center, and the Persona center; the IA center's anchor match is model-free.
6. **Access control gates every center activation and every rails IA run.**
7. **The Executive is the sole orchestrator at weight `-200`** — only one orchestrator may occupy that slot.
8. **Pipeline citizenship + curation.** The Executive curates the remaining walker queue to `{self} ∪ always_execute IAs`. `always_execute` cross-cutting IAs run as normal weight-chain members (before / after the Executive); routable IAs (anchored / non-`always_execute`) are curated out and reached only via the IA center, never self-running.

---

## 4. Worked traces

**Trivial chat — think then voice (both light):**
```
reflex: miss → Executive
Executive.tick → RESPOND(intent="greet back")     # → Persona center
Persona.tick   → voices in-identity → publish → end
# 2 light calls; optional greeting fast-path collapses to 1 (OQ #4)
```

**Skill turn — voice directly (no executive re-integration):**
```
reflex: miss → Executive
Executive.tick → ACTIVATE(Skills, "find current X", on_done="voice")  # ack optional
Skills.tick → STEP (search) → STEP (read) → RETURN(result)
   ↳ on_done="voice" → Persona voices result → end
```

**Two centers, integrated by the Executive:**
```
Executive.tick → ACTIVATE(Skills, …, on_done="integrate")
Skills … RETURN(r1)                       # → working memory
Executive.tick → ACTIVATE(IA, …, on_done="integrate")
IA … RETURN(r2)                           # → working memory
Executive.tick → RESPOND(compose(r1, r2)) # → Persona voices one coherent reply
```

**Hardened rails, sustained across turns (turn-lock):**
```
Turn N reflex: anchor "start interview" → activate IA center → runs → sustained activation persisted
Turn N+1 reflex: sustained IA activation + not an interrupt → resume IA directly (no executive, no LM)
```

---

## 5. Consequences

**Positive** — three single-responsibility centers + one executive; routing authority is singular and explicit; whole classes of bugs/knobs deleted (ping-pong, shift budget, first-emit timeout, safe-fallback ack, six-home IA dispatch, Branch A/B delivery); rails and agency unified by the reflex path; the model maps to a well-understood mental model (PFC + specialist regions + working memory), which is itself documentation.

**Negative** — we own the per-tick guarantees now (tests must prove a tick cannot sneak a second model call); only-the-Executive-activates costs an extra light integration call when chaining centers (accepted: the Executive is light and is the integrator); routing all prose through Persona makes trivial chat two light calls (mitigations in OQ #4).

**Neutral** — `PersonaAction` config is reused; the capability registry generalizes the skill catalog; skill-tier filtering replaces router pre-selection.

---

## 6. Rollout

New package `jvagent/action/executive/` (loop + registry + reflex) and `jvagent/action/executive/centers/` (skills, IA, persona) — self-contained, with no cross-imports into other action packages. New scaffolder profile `executive`; `examples/jvagent_app/agents/jvagent/executive_agent/`. The validator rejects a second orchestrator at the shared `-200` slot.

---

## 7. Open questions (for review)

1. **Naming.** `ExecutiveInteractAction` + Skills / IA / Persona *centers*. Prefer anatomical (`Cortex`, `Thalamus`, `Broca`) or functional (`Executive`, `Skills`, `Persona`)? Leaning functional names with anatomical doc analogues.
2. **`Brief` shape.** Free-text task vs. structured `{intent, slots, constraints}` for the centers.
3. **Executive provider.** Confirm light model for the executive + persona; heavy only in the Skills center.
4. **Persona egress cost.** All prose flows through Persona (maintainer choice), so trivial chat is two light calls. Build (a) non-model greeting/ack templates in Persona, (b) a fused executive+persona call on direct-response turns, or (c) accept two light calls? Also: stream Persona token-wise or flush per tick?
5. **Confidence / clarify.** When the Executive's routing confidence is low, prefer a clarifying `RESPOND` over a guess-`ACTIVATE`? Threshold + behavior.
6. **Reflex matching.** Exact/regex anchors only, or an embedding-similarity gate with a confidence floor (raises accuracy, adds a non-LM dependency to the deterministic path)?
7. **Extensibility.** Are non-terminal centers (e.g. a Memory center the Executive activates for retrieval, a Perception center for channel normalization) in scope for v1, or v2?

---

## 8. Alternatives considered

1. **Keep peers + a central arbiter** — rejected; retains peer edges and the budget machinery.
2. **Separate policy-free scheduler + conversational center** (the prior draft) — rejected for elegance; the brain doesn't separate control from the executive's own light cognition, and merging removes a concept.
3. **Let centers activate each other** — rejected; reintroduces cycles and a budget. Chaining is done by the Executive as integrator (matches PFC recruitment).
4. **Ride walker-revisit** — viable, lower-risk; deferred to the self-owned loop per the substrate decision. Reversible.
5. **Re-introduce a router stage** — rejected; the routing authority is the Executive's normal tick, not a separate classifier.

---

## 9. References
- The earlier bridge-helm and router-elimination ADRs (since removed) — the peer-shift architecture this supersedes in spirit.
- [`SPEC.md`](../SPEC.md) §3.3–§3.4, §11 — invariants re-derived or left untouched.
- [`PATTERNS.md`](../PATTERNS.md) — pattern catalog + data-driven deprecation policy.
- Baars, *A Cognitive Theory of Consciousness* (1988) — Global Workspace Theory, the functional model behind the executive-plus-specialists shape.
