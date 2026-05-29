# SkillExecutive Architecture

The **SkillExecutive** pattern is a brain-shaped, additive deployment pattern: a single model-driven orchestrator runs the whole turn over one unified tool surface. When a turn-spanning flow is in progress it surfaces that flow as a tool and lets the model decide whether to continue it, then runs a think-act-observe loop. It ships as a peer to the Rails pattern — the harness is unchanged. See [`adr/0012-skill-executive-architecture.md`](../.planning/adr/0012-skill-executive-architecture.md) for the decision record (it supersedes ADR-0010) and [`EXECUTIVE-ROADMAP.md`](../.planning/EXECUTIVE-ROADMAP.md) for the build.

## Overview

`SkillExecutiveInteractAction` (weight `-200`) is the sole orchestrator. It runs the whole turn inside one `execute()` call — no walker-revisit, no recruited centers, no separate router. The turn is a **think-act-observe loop** (one model call per tick, bounded) over a unified tool surface; routing *is* tool selection. The only twist is the turn-lock, which is realized **as a restriction on that surface**, not a separate path:

- Each turn the orchestrator detects any active flow via `continuation.active_flow_owner(visitor)` — a deterministic read of the active control-task's `owner_action`, which equals the IA's tool name.
- If a flow is active and `lock_active_flow` is on (default), the loop **restricts its callable surface to that IA's tool** and dispatches it immediately — no model round-trip (mechanistic turn-lock).
- Otherwise the loop runs normally; with `lock_active_flow=False` an active flow's tool is merely made visible alongside a guidance note (`continuation.active_flow_note(tool_name)`), leaving continuation to the model.

```
                 ┌─────────────────────────────────────────────┐
   user turn ──► │  SkillExecutiveInteractAction (-200)         │
                 │                                              │
                 │  curate walk path (drop routable IAs)         │
                 │  assemble unified tool surface                │
                 │                                              │
                 │  active flow-task & lock_active_flow?         │
                 │   ├─ yes → surface restricted to that IA's   │
                 │   │         tool; dispatch it (no model call) │
                 │   └─ no  → think-act-observe loop:            │
                 │        model decides — continue a surfaced    │
                 │        flow, or route elsewhere:              │
                 │        persona reply/respond · IA-as-tools ·  │
                 │        plain action tools · core tools ·      │
                 │        find_skill/use_skill · find_tool/load  │
                 └─────────────────────────────────────────────┘
```

Active-flow detection reads persisted state only. With `lock_active_flow=False` it is **not** a parallel router and does not force a flow to run; with `lock_active_flow=True` the loop's surface is restricted to the flow's IA tool and that tool is dispatched.

## Flow continuation (configurable: deterministic lock or model-mediated)

A *flow* is any action that wants to span turns (today: the interview). It (a) records a control-task on the conversation `TaskStore` while active (the flow does this itself — the orchestrator does not manage it), and (b) is continued by being run again. The flow's only orchestrator-facing modification is being exposed via `get_tools()` (forwarding to `execute(visitor)`) — it gains no special resume entry point, no flow-control task-type hook, and no orchestrator-specific flags.

Each turn the orchestrator detects the active flow with `continuation.active_flow_owner(visitor)` (a deterministic read of the active control-task's `owner_action`, no model). What happens next depends on the `lock_active_flow` config flag (default `True`) — see [ADR-0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md).

**`lock_active_flow=True` (default) — deterministic turn-lock.** The lock is a **tool-surface restriction inside the loop**: after assembling the surface, `_run_loop` restricts the callable tools to the owning IA's tool and dispatches it immediately (no model round-trip). The IA tool is the same visitor-bound, AC-gated, terminal `wrap_action_tool` binding used for routing — the lock reuses the unified surface, not a side path. The flow owns every turn until it clears its own task; off-topic input goes into the IA, which owns interruption/cancel (it already carries cancel/skip/update continuation intents).

**`lock_active_flow=False` — model-mediated continuation.** The orchestrator makes the flow's tool visible and injects the note from `continuation.active_flow_note(tool_name)` — roughly *"a multi-step flow is in progress; call `<tool>` to continue it if the user is engaging, otherwise handle their request normally — the flow stays active and resumes when the user returns."* The model then runs the normal loop and decides:

- **Continue** — the model selects the flow's tool, whose `get_tools` → `execute` loads and advances the flow's own session.
- **Route elsewhere** — for an off-topic utterance, the model picks a different tool (web search, reply, etc.); the flow is **not** forced to run. Its control-task persists and the flow resumes when the user returns to it. This is what prevents the "Who is Eldon Marks?" misroute, and interruptibility is automatic — there is no `can_interrupt` branch.

In both modes the flow's control-task persists across turns and is cleared only by the flow's own session logic. For the interview, continuation is just its existing `execute(visitor)` reached through its tool; it records and clears its own control-task as its session progresses. The interview is unchanged in behavior — its only orchestrator-facing surface is `get_tools()`.

## The unified tool surface

Everything the agent can do is reachable as a tool, so there is no separate router or capability registry:

| Tool family | Source | Notes |
|---|---|---|
| **Persona reply / respond** | `PersonaAction.get_tools()` | `reply` is a thin publish; `respond` is persona-framed. Model-discretionary — mostly conversational banter, since actions publish their own output. |
| **IA-as-tools** | an `InteractAction`'s own `get_tools()` | Forwards to `execute(visitor)` with the `visitor` passed through from the SkillExecutive. The tool *description* is built from the IA's manifest (`purpose` + `activates_on`, via `routing_triggers()`) so the model routes on intent. |
| **Plain action tools** | each enabled `Action.get_tools()` | Ordinary capability tools. |
| **Core tools** | [`core_tools.py`](../jvagent/action/skill_executive/core_tools.py) | Built-in orchestrator services. |
| **Skills + meta-tools** | native SOP skills + catalog | `find_skill` / `use_skill` and `find_tool` / `load_tool` for progressive disclosure. |

### Manifest as the routing signal

An IA's `get_tools()` builds the tool's description from its **manifest**
(`purpose` + `activates_on` entry intents) via `InteractAction.routing_triggers()`,
so the model selects it on intent without a separate anchor router.
`routing_triggers()` uses `manifest.activates_on` (falling back to static
`anchors` only when no manifest is declared) and never includes runtime-merged
continuation anchors (cancel/update/confirm/skip/decline) — those describe
in-flow behavior, and including them would bloat the description and make the
relevance gate over-match. The same triggers feed the SkillExecutive's
visibility gate. First-entry and continuation are both model-judged.

### Progressive disclosure (the tool catalog)

A **tool catalog** (mirroring the skills catalog) exposes `find_tool` / `load_tool` so the prompt carries a slim index rather than every tool schema — bounding prompt size as the surface grows. The skills meta-tools (`find_skill` / `use_skill`) work the same way for native SOP skills.

## Invariants (SPEC §3.3)

1. **One model call per tick**, loop-enforced via a per-tick `ModelBudget`; the loop is bounded by an activation budget.
2. **Flow continuation mode is configurable** via `lock_active_flow` ([ADR-0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md)). Active-flow detection (`active_flow_owner`) is always a deterministic read of persisted `TaskStore` state (no model).
3. **Turn-lock is deterministic when `lock_active_flow=True`** (default — the loop restricts its callable surface to the active flow's IA tool and dispatches it with no model round-trip) and **emergent/model-mediated when `False`** (the flow's tool is surfaced and the model decides whether to continue or detour). In both modes the control-task persists across turns and is cleared only by the flow's own session logic.
4. **Routing is tool selection.** There is no separate router or capability registry; IAs, persona, core services, and skills are all tools.
5. **Actions own their output.** Actions publish their own results; the `reply`/`respond` persona tools are model-discretionary. A turn that ends with no emission and no active flow gets a single fallback reply.
6. **Access control gates tool dispatch** (`tool:*`), including IA-as-tool execution (`tool:delegate:{name}` preserved).

## Configuration

```yaml
actions:
  - action: jvagent/skill_executive
    context:
      enabled: true
      activation_budget: 16
      model: gpt-4o-mini
      model_action_type: OpenAILanguageModelAction
      lock_active_flow: true     # deterministic turn-lock; false = model-mediated
      skills_source: both        # both|local|app|registry|builtin
  - action: jvagent/openai_lm
    context: { enabled: true }
  - action: jvagent/persona
    context: { enabled: true }
  - action: jvagent/intro
    context: { enabled: true }
  - action: jvagent/handoff
    context: { enabled: true }
```

The scaffold default profile is still `executive`, but it now contains a single `jvagent/skill_executive` action (plus `openai_lm`, `persona`, `intro`, `handoff`). Scaffold with `jvagent app create --profile executive`; see the reference agent at `examples/jvagent_app/agents/jvagent/executive_agent/`, which uses `jvagent/skill_executive`.

## Module structure

```
jvagent/action/skill_executive/
  ├─ skill_executive_interact_action.py  # orchestrator: walk-path curation + tool-surface assembly + loop
  ├─ continuation.py                     # active-flow surfacing (active_flow_owner + active_flow_note)
  ├─ tools.py                            # SkillTool primitives + wrap/parse/render helpers
  ├─ core_tools.py                       # built-in orchestrator core tools
  ├─ catalog.py                          # tool catalog (find_tool/load_tool)
  ├─ skills.py                           # native SOP skill discovery + find_skill/use_skill
  ├─ prompts.py                          # orchestrator + loop prompts
  ├─ access.py                           # tool:* / tool:delegate AC
  └─ info.yaml                           # package metadata
```

## Skills (native SOP overlay)

A skill is **judgment over capability, not capability** (ADR-0011). Tools answer "can I do X"; a skill is a standard operating procedure that *coordinates* the tools the agent already has. So a jvagent-native skill is a `SKILL.md` body that references action tools by their `namespace__tool` name and carries no executable code.

- Discovery: [`jvagent/action/skill_executive/skills.py`](../jvagent/action/skill_executive/skills.py) reuses the neutral `jvagent.scaffold.skill_resolve` (built-in `jvagent.skills` + app-local `agents/<ns>/<agent>/skills/*`). Config: `skills_source` (`both|local|app|registry|builtin`) plus a `skills` selector — no explicit per-skill list.
- Exposure: the orchestrator adds `find_skill` / `use_skill` meta-tools (progressive disclosure). `use_skill` returns the SOP body as an observation, so it persists for the rest of the loop. No change to routing or the one-call-per-tick contract.
- `allowed-tools` is a **soft dependency** — a skill activates even if a referenced tool is missing, but the loop warns so the model won't follow an unexecutable step.

## Known follow-ups

- Action tools and native SOP skills are both wired. **Self-contained Claude skill bundles** (SKILL.md + scripts in a sandbox) are a separate substrate — deferred to a future wave (ADR-0011).
- First-entry routing accuracy now depends on model tool-selection (anchors-in-description + a routing nudge + tests mitigate this); trivial-turn latency, since every non-flow turn enters the loop (mitigated by the slim tool catalog and a `converse` fast-reply skill). Both measured at rollout.
- Live-provider smoke + a performance ledger entry (the in-tree smoke mocks leaf model calls).
