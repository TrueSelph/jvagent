# Executive Architecture

The **Executive** pattern is a brain-shaped, additive deployment pattern: one central executive recruits specialist *centers* and voices through a single persona egress. It ships as a peer to Rails, Cockpit, and Bridge — the harness is unchanged. See [`adr/0010-executive-centers-architecture.md`](../.planning/adr/0010-executive-centers-architecture.md) for the decision record and [`EXECUTIVE-ROADMAP.md`](../.planning/EXECUTIVE-ROADMAP.md) for the build.

## Overview

`ExecutiveInteractAction` (weight `-200`, mutually exclusive with Bridge/Cockpit) owns a **frame-stack control loop** that runs the whole turn inside one `execute()` call — no walker-revisit. It is the agent's prefrontal cortex: it engages trivial conversation, knows all centers via a capability registry, holds working memory, activates centers (the only component that does), integrates their results, and decides when to respond.

```
Reflex (no model): anchor hit / open session?  ──► activate that center directly
        │ miss
        ▼
   EXECUTIVE (light model)  ──ACTIVATE──►  CENTER (leaf)
        ▲                                    │ STEP (more work) / RETURN (result)
        └──────── working memory ◄───────────┘
        │
        └── RESPOND ──► PERSONA center (sole egress) ──► user
```

## Centers

A center is a `BaseCenter` (an `Action`, not an `InteractAction`). The executive recruits it via `await center.tick(ctx, frame)` once per activation tick; the center returns `STEP` (recruit me again) or `RETURN(Result)` (done). Centers are **leaves** — they never activate one another.

| Center | Role | Model |
|---|---|---|
| **PersonaCenter** | language/identity — the sole egress for all user-facing prose (wraps `PersonaAction`; `verbatim` bypass) | light |
| **SkillsCenter** | skill-based reasoning — bounded think-act-observe over a tool surface | heavy |
| **IACenter** | anchored rails-IA authority — resolves, AC-gates, and runs hardened `InteractAction` pathways; reports turn-lock as sustained activation | usually none |

## Verbs

Role-typed (ADR-0010 §2.2). The Executive emits `ACTIVATE | RESPOND | YIELD`; a center emits `STEP | RETURN`. `ACTIVATE(on_done=…)` chooses whether a center's result is voiced directly (`"voice"`) or returned to the executive to integrate (`"integrate"`).

## Reflex and turn-lock

Before any model call, a deterministic **reflex** pre-pass runs: a *sustained activation* (turn-lock) from a prior turn is resumed (unless the utterance is an interrupt phrase), else a high-confidence **anchor** hit routes straight to the IA center, else control falls to the Executive. Sustained activation is persisted on the conversation's declarative **`TaskStore`** as an `executive_sustained` task; an IA flow reuses the rails IA's *own* task (no duplicate). The reflex resumes whichever center owns the active task. (`jvagent/action/executive/sustained.py`.)

## Pipeline citizenship + curation

The Executive curates the remaining walker queue to `{self} ∪ always_execute IAs`. `always_execute` cross-cutting IAs (auth, intro, audit, analytics) run as ordinary weight-chain members before (`weight < -200`) and after (`weight > -200`) the Executive. **Routable IAs (anchored / non-`always_execute`) are curated out** and reached only through the IA center — they never self-run. (Without this, with no `InteractRouter` gating top-level IAs, an anchored flow self-executes every turn alongside the Executive — a live-smoke finding, 2026-05-29.)

## Configuration

```yaml
actions:
  - action: jvagent/executive
    context:
      enabled: true
      centers: [SkillsCenter, IACenter, PersonaCenter]
      persona_center: PersonaCenter
      ia_center: IACenter
      activation_budget: 16
      model: gpt-4o-mini
      model_action_type: OpenAILanguageModelAction
  - action: jvagent/skills_center
    context: { enabled: true, model: gpt-4o-mini, max_iterations: 8 }
  - action: jvagent/ia_center
    context: { enabled: true }
  - action: jvagent/persona_center
    context: { enabled: true }
```

Scaffold a new agent with `jvagent app create --profile executive`; see the reference agent at `examples/jvagent_app/agents/jvagent/executive_agent/`.

## Invariants (SPEC §3.5)

1. One model call per tick (loop-enforced `ModelBudget`).
2. Only the Executive activates centers; centers are leaves (no cycles; bounded by `activation_budget`).
3. Working memory is the per-turn state; sustained activation (turn-lock) is persisted on the conversation `TaskStore` (an `executive_sustained` task, or the rails IA's own task), not in working memory.
4. The Persona center is the only path to final prose.
5. The reflex pre-pass is deterministic (no model).
6. Access control gates every center activation (`tool:center:{name}`) and every rails IA run (`tool:delegate:{name}`).
7. Pipeline citizenship — the queue is curated to `{self} ∪ always_execute IAs`; routable IAs run only via the IA center.

## Module structure

```
jvagent/action/executive/
  ├─ executive_interact_action.py   # ExecutiveInteractAction + control loop
  ├─ contracts.py                   # verbs, Brief, Result
  ├─ state.py                       # WorkingMemory, Frame, ModelBudget
  ├─ context.py                     # TurnContext
  ├─ registry.py                    # CapabilityRegistry + anchor matching
  ├─ access.py                      # tool:center / tool:delegate AC
  ├─ prompts.py                     # executive + skills prompts
  ├─ base.py                        # BaseCenter
  ├─ stub_center.py                 # test fixture
  └─ centers/
       ├─ persona_center.py / persona/    # PersonaCenter (egress)
       ├─ skills_center.py  / skills/      # SkillsCenter
       └─ ia_center.py      / ia/          # IACenter
```

## Skills (native SOP overlay)

A skill is **judgment over capability, not capability** (ADR-0011). Tools answer "can I do X"; a skill is a standard operating procedure that *coordinates* the tools the agent already has. So a jvagent-native skill is a `SKILL.md` body that references action tools by their `namespace__tool` name and carries no executable code.

- Discovery: `jvagent/action/executive/skills_catalog.py` (`discover_skill_docs`) reuses the neutral `jvagent.scaffold.skill_resolve` (built-in `jvagent.skills` + app-local `agents/<ns>/<agent>/skills/*`). Config: `skills_source` (`both|local|app|registry|builtin`) + `skills` selector on the Skills center — no explicit per-skill list.
- Exposure: the Skills center adds `find_skill` / `use_skill` meta-tools (progressive disclosure). `use_skill` returns the SOP body as an observation, so it persists for the rest of the loop. No change to routing or the one-call-per-tick contract.
- `allowed-tools` is a **soft dependency** — a skill activates even if a referenced tool is missing, but the center warns so the model won't follow an unexecutable step.

## Known follow-ups

- Action tools and native SOP skills are both wired. **Self-contained Claude skill bundles** (SKILL.md + scripts in a sandbox) are a separate substrate — deferred to a future wave (ADR-0011).
- `_build_registry` enumerates anchored rails IAs; skill capabilities are not yet enumerated into the *routing* registry (they're discovered inside the Skills center instead).
- Live-provider smoke + a performance ledger entry vs Bridge (the in-tree smoke mocks leaf model calls).
