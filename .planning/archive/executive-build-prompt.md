# Build prompt — Executive + Centers pattern (ADR-0010)

> ⚠️ **SUPERSEDED — historical record.** This build prompt targets the
> **Executive + Centers** architecture of [ADR-0010](../adr/0010-executive-centers-architecture.md),
> which was **superseded** by the single-orchestrator **SkillExecutive** pattern
> ([ADR-0012](../adr/0012-skill-executive-architecture.md), refined by ADR-0013/0014/0015/0016).
> Do **not** build from this. The shipped v1 architecture — one orchestrator at
> weight `-200`, a unified tool surface, no recruited "centers" — is documented in
> [`../docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md). This file is retained only to
> preserve the original design intent, mirroring how ADR-0010 itself is kept.
>
> Paste this to Claude Code (or any coding agent) at the repo root of `jvagent`. It is self-contained but assumes the repo's `CLAUDE.md`, `.planning/SPEC.md`, and `.planning/adr/0010-executive-centers-architecture.md` are present.

---

## Mission

Implement the **Executive + Centers** deployment pattern specified in `.planning/adr/0010-executive-centers-architecture.md`. It is a new, additive pattern that ships **alongside** the Rails pattern — no forced migration, no harness subsumption. ADR-0010 is the **source of truth**; if anything below conflicts with it, the ADR wins and you flag the conflict.

Read first, in order: `CLAUDE.md`, `.planning/adr/0010-executive-centers-architecture.md`, `.planning/SPEC.md` (§3.3–§3.4, §11).

## Mental model (so you make correct judgment calls)

A brain. One **Executive** (prefrontal cortex, light model) engages trivial conversation, knows all **centers** via a registry, holds **working memory**, activates centers, integrates results, and decides when to respond. Centers are specialist **leaves**: **Skills** (skill reasoning, heavy model), **IA** (hardened anchored rails pathways), **Persona** (language/identity — the sole egress). A deterministic **reflex** path (anchors, open sessions) bypasses the Executive. Only the Executive activates centers; centers `STEP` or `RETURN`.

## Hard constraints (non-negotiable; tests must enforce)

1. **Isolation.** New code lives under `jvagent/action/executive/`. Re-implement everything it needs locally rather than reaching into other action packages. Existing patterns' code, tests, and runtime behavior stay byte-for-byte unchanged.
2. **One model call per tick.** Enforce mechanically via a `model_budget(max_calls=1)` guard around each tick; a second call in one tick raises and aborts the tick. Write a test that proves a misbehaving center cannot sneak a second call.
3. **Only the Executive activates centers.** Centers are leaves (`STEP`/`RETURN` only). No center→center activation. The activation graph is a depth-1 star.
4. **Persona is the sole egress.** No center (Executive included) publishes final user-facing prose directly; everything routes through the Persona center. Transient `ack`s on activation are the only exception.
5. **Pipeline citizenship.** The Executive is a normal `InteractAction` at `weight=-200`. Do **not** curate/seize the walker queue (no `_curate_walker_queue` equivalent). Unanchored `always_execute` IAs must run as normal weight-chain members before and after the Executive. The Executive runs its loop inside one `execute()` and returns.
6. **Mutual exclusivity.** Only one orchestrator may occupy the shared `-200` slot. The agent-yaml validator must warn/reject a second.
7. **SPEC.** Do not modify invariants 1–13 or §3.3/§3.4. Add a new **§3.5 "Executive scheduler pattern"** with the invariant block from ADR-0010 §3. Honor §11 inv. 4 — never recursively invoke the walker; loop internally and return once.
8. **Repo conventions.** Type-annotate everything; persist Node fields via `attribute(...)`; honor lifecycle hooks; default analytics/follow-ups to `run_in_background=True`; `len(await Entity.find(...))` not `count()`; `await App.get()` for the singleton. `pre-commit run --all-files` green before any milestone is "done." Commit per milestone with `file:line` citations in the message.

## Defaults for ADR-0010 open questions (use these; don't block)

- **Brief**: a small dataclass `Brief(intent: str, slots: dict = {}, constraints: list = [])`. Start free-text-friendly; keep it extensible.
- **Executive + Persona provider**: light model, configurable via `model` / `model_action_type` (default `gpt-4o-mini`). Heavy model only in the Skills center.
- **Persona egress cost**: accept two light calls for trivial chat in v1. Leave a clean seam for a future non-model greeting fast-path; do not build it yet.
- **Reflex matching**: exact + regex anchors in v1 (no embeddings). Leave an interface seam for a similarity gate later.
- **Confidence/clarify**: when Executive routing confidence is low, prefer a clarifying `RESPOND` over a guess-`ACTIVATE`.
- **Extensibility**: design the center interface so new centers (Memory, Perception) can be added later. Ship only Skills, IA, Persona in v1.

## Plan — milestones (each: deliverables → tests → exit gate). Do them in order; stop at each gate.

**M0 — Roadmap + contracts.** Write `.planning/archive/EXECUTIVE-ROADMAP.md` (status table, constraints, milestones, exit gates). Define contracts in `jvagent/action/executive/contracts.py`: the verbs (`ACTIVATE`, `RESPOND`, `YIELD`, `STEP`, `RETURN`), `Brief`, `Result`, `WorkingMemory`, and the `Center` base protocol (`async def tick(ctx, wm) -> CenterDirective`). Add SPEC §3.5. *Tests:* none (design). *Exit:* contracts compile, mypy clean, ADR/SPEC/roadmap cross-reference correctly.

**M1 — Executive skeleton + stub centers.** `ExecutiveInteractAction(InteractAction)` at `weight=-200` with the control loop from ADR-0010 §2.3; `model_budget`, `gate_access`, `record_tick`, `flush_stream`, `activation_budget`; a `StubCenter` for deterministic tests. No real LM calls. *Tests* (`tests/action/executive/`): verb dispatch; one-call-per-tick enforcement (second call aborts); activation budget bound; AC gates each ACTIVATE; pipeline citizenship (a pre- and post- `always_execute` IA both run; queue is NOT curated); mutual-exclusivity validator. Aim 100% coverage on new code, zero real model calls. *Exit:* a full turn runs end-to-end with stub centers; all the above tests pass.

**M2 — Capability registry + reflex.** `CapabilityRegistry` (skills + IAs + anchors, tiered routing view vs execution view) and the deterministic `reflex(utterance, registry)` pre-pass (exact + regex anchors, open-session resume). No LM. *Tests:* registry projection/tiering; reflex hits route directly to the right center; reflex miss falls through to the Executive; suspended-session resume. *Exit:* hardened anchored path works with stub centers and no model call.

**M3 — Persona center (egress).** Reuse `PersonaAction` for identity/voice; the Persona center is the only publisher. Implement `RESPOND` and `on_done="voice"` routing. *Tests:* every user-facing publish goes through Persona; `verbatim` skips restyle; no center publishes directly. *Exit:* stub Executive + Persona produces a voiced reply.

**M4 — Executive (light LM).** Real Executive tick: structured output that either `RESPOND`s (trivial chat) or `ACTIVATE`s a center, reading the tiered routing view + history + working memory; low-confidence → clarify. *Tests:* fixture utterances → expected verb; trivial chat = one Executive call then Persona; routing picks the right center from the registry. *Exit:* live smoke shows trivial chat and a single-center dispatch working.

**M5 — Skills center (heavy LM).** Think-act-observe over the skill surface (re-implement the loop; do not import reasoning). `STEP` per tool round, `RETURN(result)` on completion; bounded by the activation budget and its own iteration cap. *Tests:* multi-step skill task; tool dispatch; stuck/cap termination; result lands in working memory or voices directly. *Exit:* a web-search-style skill turn completes via the Executive.

**M6 — IA center (rails).** The single anchored-IA authority: anchor match → run rails IA (chains allowed internally) → `RETURN` or suspend (sustained activation = turn-lock) in working memory. Collapses the previously scattered IA-dispatch homes into one center. *Tests:* anchored IA runs; multi-turn interview suspends and resumes via reflex next turn; interrupt phrase breaks the lock; AC gates each IA run. *Exit:* interview scenario works end-to-end through the Executive.

**M7 — Working-memory persistence.** Persist sustained activation on the Conversation; rehydrate at turn start; clear on completion/cancel. *Tests:* turn-lock survives across turns; dead session does not re-trigger. *Exit:* cross-turn continuity proven.

**M8 — Scaffolder profile + example.** `executive` profile in `jvagent/scaffold/builtin_profiles/`; `examples/jvagent_app/agents/jvagent/executive_agent/`. *Tests:* scaffold smoke; `jvagent ... validate` passes; a second orchestrator at `-200` rejected. *Exit:* `jvagent app create --profile executive` yields a working agent.

**M9 — Observability + docs + parity.** Activation-trace events on `Interaction`; `docs/ORCHESTRATOR.md`; `PATTERNS.md` + `GLOSSARY.md` + top-level `CLAUDE.md` updated; a smoke harness (`tests/action/executive/smoke_executive.py`) running the 6-utterance suite, archived under `baselines/`. *Exit:* a turn is fully traceable from one log query; smoke runs clean; pattern documented as a peer.

## Testing & verification

- Unit tests under `tests/action/executive/`. Real-LM smoke separate and clearly marked.
- After each milestone: `pytest tests/action/executive/ -v`, then the full suite to prove **no regressions**, then `pre-commit run --all-files`.
- Final gate: spawn a fresh subagent to review the whole pattern against ADR-0010 §3 invariants and the hard constraints above.

## Working style

Branch `dev-executive`. One commit per milestone with `file:line` citations. Don't gold-plate — ship the v1 defaults above and leave seams (not implementations) for the deferred options. If you hit a genuine fork not covered here or in the ADR, stop and ask rather than guess.
