# Orchestrator Architecture

The **Orchestrator** pattern is a brain-shaped, additive deployment pattern: a single model-driven orchestrator runs the whole turn over one unified tool surface. When a turn-spanning flow is in progress it surfaces that flow as a tool and lets the model decide whether to continue it, then runs a think-act-observe loop. It ships as a peer to the Rails pattern — the harness is unchanged. See [`adr/0012-skill-executive-architecture.md`](../.planning/adr/0012-skill-executive-architecture.md) for the decision record (it supersedes ADR-0010) and [`EXECUTIVE-ROADMAP.md`](../.planning/archive/EXECUTIVE-ROADMAP.md) for the build. Two later ADRs refine the surface: [ADR-0017](../.planning/adr/0017-two-skill-specs-code-execution-substrate.md) (two skill specs + a multitenant code-execution substrate) and [ADR-0018](../.planning/adr/0018-lean-tool-surfacing.md) (lean tool surfacing).

## Overview

`OrchestratorInteractAction` (weight `-200`) is the sole orchestrator. It runs the whole turn inside one `execute()` call — no walker-revisit, no recruited centers, no separate router. The turn is a **think-act-observe loop** (one model call per tick, bounded) over a unified tool surface; routing *is* tool selection. The only twist is the turn-lock, which is realized **as a restriction on that surface**, not a separate path:

- Each turn the orchestrator detects any active flow via `continuation.active_flow_owner(visitor)` — a deterministic read of the active control-task's `owner_action`, which equals the IA's tool name.
- If a flow is active and `lock_active_flow` is on (default), the loop **restricts its callable surface to that IA's tool** and dispatches it immediately — no model round-trip (mechanistic turn-lock).
- Otherwise the loop runs normally; with `lock_active_flow=False` an active flow's tool is merely made visible alongside a guidance note (`continuation.active_flow_note(tool_name)`), leaving continuation to the model.

```
                 ┌─────────────────────────────────────────────┐
   user turn ──► │  OrchestratorInteractAction (-200)         │
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

## Resumable planning (`planning`, ADR-0019)

Where a *flow* is an IA that owns turns, a **plan** is the orchestrator's own multi-step work made resumable. It is **opt-in** (`planning: true`, default off) so a lean agent pays nothing. When on, the orchestrator surfaces an `update_plan` tool: the model records a checklist that persists across turns as an `AGENTIC_LOOP` control-task on the same `TaskStore` (owned by the orchestrator, distinct from IA flows). The model re-sends the full checklist each call (TodoWrite-style overwrite), so there is ever only one active plan.

Resume is **soft**, mirroring `lock_active_flow=False`: at turn start, an unfinished plan is re-surfaced via `continuation.plan_resume_note(...)` — *"continue from the first unfinished step, don't redo completed ones; if the user changed topic, the plan stays parked."* The loop's `finally` runs `_finalize_plan`: a fully-done plan is completed and cleared; a plan with pending steps is left **active**, so a turn cut short by budget/duration (or a crash — each `update_plan` call persists) resumes next turn instead of re-planning. `AGENTIC_LOOP` stays excluded from IA-flow routing (there is no IA tool to call). Side-effect idempotency on resume is out of scope — resume avoids redoing *steps*, not already-executed tool side effects. See [ADR-0019](../.planning/adr/0019-orchestrator-resumable-plan.md).

## The unified tool surface

Everything the agent can do is reachable as a tool, so there is no separate router or capability registry:

| Tool family | Source | Notes |
|---|---|---|
| **Egress reply / respond** | the responder's `get_tools()` — `ReplyAction` (ADR-0014), or `PersonaAction` fallback | `reply` is the send path — slim thin-publish, or applies pending directives/parameters when present; `respond` voices text in the agent's identity. Resolved via `Action.get_responder()`. |
| **IA-as-tools** | an `InteractAction`'s own `get_tools()` | Forwards to `execute(visitor)` with the `visitor` passed through from the Orchestrator. The tool *description* is built from the IA's manifest (`purpose` + `activates_on`, via `routing_triggers()`) so the model routes on intent. |
| **Plain action tools** | each enabled `Action.get_tools()` | Ordinary capability tools. |
| **Core tools** | [`core_tools.py`](../jvagent/action/orchestrator/core_tools.py) | Built-in orchestrator services. |
| **Skills + meta-tools** | skills (two specs: JV + Claude) + catalog | `find_skill` / `use_skill` and `find_tool` / `load_tool` for progressive disclosure (ADR-0017). |

### Manifest as the routing signal

An IA's `get_tools()` builds the tool's description from its **manifest**
(`purpose` + `activates_on` entry intents) via `InteractAction.routing_triggers()`,
so the model selects it on intent without a separate anchor router.
`routing_triggers()` uses `manifest.activates_on` (falling back to static
`anchors` only when no manifest is declared) and never includes runtime-merged
continuation anchors (cancel/update/confirm/skip/decline) — those describe
in-flow behavior, and including them would bloat the description and make the
relevance gate over-match. The same triggers feed the Orchestrator's
visibility gate. First-entry and continuation are both model-judged.

### Progressive disclosure (the tool catalog)

A **tool catalog** (mirroring the skills catalog) exposes `find_tool` / `load_tool` so the prompt carries a slim index rather than every tool schema — bounding prompt size as the surface grows. The skills meta-tools (`find_skill` / `use_skill`) work the same way for both skill specs (JV + Claude).

**Lean tool surfacing (ADR-0018).** The catalog only helps if tools are actually hidden. When the count of hideable capability tools (action + MCP) exceeds `lean_tool_threshold` (default 15), the prompt lists only the always-on core (egress, meta-tools, core tools, an active-flow tool) plus a per-turn **relevance pre-surface** — the `lean_presurface_k` (default 6) tools whose name+description best overlap the user's message (cheap token match, no model call). The long tail stays on the full surface, reachable via `find_tool` (output grouped by namespace), and a one-line hint tells the model the list is partial. Below the threshold every tool is listed (unchanged); `lean_tool_threshold: 0` disables it. Dispatch already resolves against the full surface, so hiding a tool from the prompt never makes it uncallable — only the listing shrinks, keeping each tick small on large-surface agents.

Two further knobs/notes:

- **Skills are not gated** by lean surfacing — they're few and the listing is the proactive "prefer a whole SOP" nudge, so the skill index stays fully shown. `find_skill` remains for larger catalogs.
- **Essentials-only** is reachable with `lean_presurface_k: 0` (optionally `lean_tool_threshold: 1` to force it on for any surface): the prompt then carries only egress, the meta-tools (`find_*`/`load_tool`/`use_skill`), core tools, an active-flow tool, and the skill index — every capability is reached via `find_tool`. It's the smallest prompt but pays a discovery round-trip on most turns and leans harder on weaker models, so it's an **option for very large surfaces / strong models**, not the default. The default relevance pre-surface removes that round-trip for the common single-intent turn, which is where a lean harness should be fast.
- **Always-visible pins (turn-1 immediacy).** The relevance pre-surface is lexical, so a tool that must be callable on the first turn *regardless of phrasing* (e.g. a filing tool) can fall behind a `find_tool` round-trip. Rather than un-leaning the whole surface with `lean_tool_threshold: 0`, pin just the few that need it — `pinned_tools: ["filing__*"]` (tool-name globs) or a `SKILL.md` with `always-active: true` (pins that skill's `allowed-tools`). Both are merged into the visible set *after* the lean policy, every turn, and default off. (`always-active` previously did nothing to tool visibility under the orchestrator — it now pins, per [ADR-0018 §5](../.planning/adr/0018-lean-tool-surfacing.md).)

## Identity and egress (ADR-0014)

Identity and voicing are split along two axes:

- **Identity lives on the Agent node** — `alias` (display name) + `role` (purpose). The Orchestrator injects *"You are {alias}, {role}."* at the head of its system prompt (`render_identity_section`), so the model reasons and writes **as the agent** from the first token. The same fields are read by the egress voice — one source, no duplication.
- **Egress is a `ReplyAction`** (`jvagent/reply`) — the agent's *mouth* and the Orchestrator's send path. `reply` delivers the user's message: **slim** (a thin literal publish, no model call) by default, but when there's shaping to apply it composes via `respond` — pending **directives** (mandatory instructions), **parameters** (conditional rules), and channel **formatting**. Channel formats live in `CHANNEL_FORMATS` (overridable per channel via the `channel_formats` attribute); the default/web channel carries none, so ordinary turns stay slim for token efficiency, while voice/SMS/social channels get plain-text or channel-specific markup. `publish` is the egress primitive.
- **Resolution is `Action.get_responder()`** — prefers `ReplyAction`, falls back to `PersonaAction`. The Orchestrator resolves the responder for its `reply`/`respond` tools and for `_finalize_directives` (which hands rails directive text to `respond`). `PersonaAction` is unchanged and remains the egress for Rails agents.

The reference agent use `jvagent/reply`; `PersonaAction` stays installable for Rails.

## Streaming emission (chat-UI contract)

The orchestrator publishes a typed stream over the response bus so a chat UI (jvchat, or any assistant-ui / Vercel-AI-SDK client via a thin translator) can render reasoning, tool activity, and acks distinctly from the answer. Every bus message carries `category` ∈ {`user`, `thought`} and, for thoughts, a `thought_type`:

| Emission | `category` | `thought_type` | Notes |
|---|---|---|---|
| Answer text | `user` | — | the persisted reply (`reply`/`respond`) |
| Reasoning / progress line | `thought` | `reasoning` | per-tick when `stream_internal_progress`; both gears (so light single-step turns still show reasoning) |
| Tool call (before dispatch) | `thought` | `tool_call` | `metadata.tool_name`, `.tool_args`; carries a `segment_id` |
| Tool result (after dispatch) | `thought` | `tool_result` | `metadata.tool_name`, `.tool_result`, `.is_error`; **same `segment_id`** so the pair folds into one UI element. Substantive tools only (egress/meta excluded) |
| "Working on it" ack | see note | `status` / — | **channel-conditional** |

**Channel-conditional acks.** On a **streamed** UI (`visitor.stream`) the ack is `category="thought", thought_type="status"` — an ephemeral activity-strip line, kept out of the answer transcript. On a **non-streamed** channel (WhatsApp, etc.) there is no activity strip, so the ack is published as a whole `category="user"` message (delivered by the channel adapter; `transient` ⇒ not persisted to `interaction.response`). Thoughts are not relayed to channel adapters unless `relay_to_adapters` + the adapter opts in, so reasoning/tool traces never leak to WhatsApp.

All thought emissions are `transient` (they land in `interaction.agent_trace`, never `interaction.response`). See the [orchestrator stream-emission spec](../.planning/) and `tests/action/orchestrator/test_stream_emission.py`.

## Invariants (SPEC §3.3)

1. **One model call per tick**; the loop is bounded by ``activation_budget`` (each tick is at most one model round-trip).
2. **Flow continuation mode is configurable** via `lock_active_flow` ([ADR-0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md)). Active-flow detection (`active_flow_owner`) is always a deterministic read of persisted `TaskStore` state (no model).
3. **Turn-lock is deterministic when `lock_active_flow=True`** (default — the loop restricts its callable surface to the active flow's IA tool and dispatches it with no model round-trip) and **emergent/model-mediated when `False`** (the flow's tool is surfaced and the model decides whether to continue or detour). In both modes the control-task persists across turns and is cleared only by the flow's own session logic.
4. **Routing is tool selection.** There is no separate router or capability registry; IAs, persona, core services, and skills are all tools.
5. **Actions own their output.** Actions publish their own results; the `reply`/`respond` egress tools (from the responder — `ReplyAction` or `PersonaAction` fallback, ADR-0014) are model-discretionary. A turn that ends with no emission and no active flow gets a single fallback reply.
6. **Access control gates tool dispatch** (`tool:*`), including IA-as-tool execution (`tool:delegate:{name}` preserved).

## Configuration

```yaml
actions:
  - action: jvagent/orchestrator
    context:
      enabled: true
      activation_budget: 24       # max tool-using ticks/turn; raise for research
      model: gpt-4o-mini
      model_action_type: OpenAILanguageModelAction
      lock_active_flow: true     # deterministic turn-lock; false = model-mediated
      planning: false            # true → surface update_plan; persist+resume plans (ADR-0019)
      skills_source: both        # both | app | library
  - action: jvagent/openai_lm
    context: { enabled: true }
  - action: jvagent/reply            # egress voice (ADR-0014); identity from the Agent
    context: { enabled: true }
  - action: jvagent/intro
    context: { enabled: true }
  - action: jvagent/handoff
    context: { enabled: true }
  - action: jvagent/serper_web_search    # search the web (titles/links/snippets)
    context: { enabled: true }
  - action: jvagent/web_fetch            # read a source in full after searching
    context: { enabled: true }
```

Pair `web_search` with `web_fetch`: search surfaces URLs, then `web_fetch__fetch` reads the top sources as clean markdown — far more efficient (and better grounded) than re-searching snippets. `web_fetch` is SSRF-guarded by default (blocks loopback/private/link-local hosts) and frames fetched text as untrusted so it composes with the loop's anti-injection boundaries.

### Model gearing (ADR-0016)

Optional: pair a **light** completion model with the **heavy** reasoning model so single-dimensional turns don't pay the reasoning tax. The existing `model*`/`reasoning_*` are the heavy profile; set `light_model` (+ `light_model_action_type`, `light_model_temperature`, `light_model_max_tokens`) to engage gearing — empty leaves the agent single-model. The loop starts on the light model and **escalates to heavy** once the turn is multi-step: `escalate_after_tool_calls` substantive tool calls (default 2; egress/meta tools excluded) or `escalate_on_skill` (a skill activated). Escalation is sticky; the partial-compose finalize runs light. Reasoning kwargs apply only on the heavy gear. The `orchestrator_activation` event reports `ticks_light`/`ticks_heavy`/`escalated`.

```yaml
      model: kimi-k2.6:cloud            # heavy / reasoning
      model_action_type: OllamaLanguageModelAction
      light_model: gpt-4o-mini          # light / completion (engages gearing)
      light_model_action_type: OpenAILanguageModelAction
      escalate_after_tool_calls: 2
      escalate_on_skill: true
```

### Extended config surface (ADR-0015)

All off/neutral by default — the reference agent is unchanged. Full table in [configuration-keys.md §6](../.planning/reference/configuration-keys.md).

- **Reasoning** (reasoning-capable models only): `reasoning_enabled`, `reasoning_effort` (low/medium/high), `reasoning_budget_tokens`, `reasoning_extra`. Threaded into the loop's model call; the executive profile owns its own reasoning level.
- **Thinking stream** (needs a live bus): `stream_internal_progress` emits each tick as a transient `thought`; `stream_reasoning_trace` surfaces `result.thinking_content`.
- **Budgets**: `activation_budget` (max tool-using ticks/turn, default **24** — each tick is one tool call, so multistep research/agentic work wants 30–50; the repeat-guard bounds runaway loops). `model_max_tokens` defaults to **4096** — the orchestrator is agentic (each tick emits reasoning plus an action, often the substantive answer, and thinking models spend completion tokens on reasoning), so it carries more headroom than a single-shot responder; raise further for long-form replies. `max_duration_seconds` (wall-clock, alongside the tick budget), `max_statement_length` (soft prompt cap), `history_limit` (loop working context; the rolling memory window is the agent-level `interaction_limit`). When a turn exhausts its budget or time mid-task, the loop **forces one partial-compose** — it replies with the agent's best answer from what it gathered rather than dropping to the generic clarify fallback.
- **Tooling / UX**: `tool_tier` (minimal/standard/full), `tool_call_timeout`, `enable_transient_ack` + `first_emit_timeout_ms` + `ack_statements`. `block_raw_tool_invocation` does two things: (1) only surfaced (visible) tools are callable — hidden ones need `find_tool`/a skill; and (2) it adds a **tool-use policy** to the loop prompt so the user can't steer tool selection — naming a tool/function/argument is treated as intent, not a command; the user states a goal and the agent chooses the tools.
- **MCP tool servers**: `tool_servers` (`-all` or action-name list) pulls tools from `jvagent/mcp` `MCPAction`(s); they surface as `mcp_<server>__<tool>` and route per-user (the loop binds the dispatch context for the turn). `max_concurrent_tools` is reserved for future parallel tool batches (the loop executes one tool per tick today).

```yaml
  - action: jvagent/mcp           # sandboxed MCP gateway (declares the `mcp` pip extra)
    context:
      enabled: true
      sandbox_mode: true
      sandbox_user_scoped: true
      servers:
        - name: filesystem
          transport: stdio
          command: npx
          args: [-y, "@modelcontextprotocol/server-filesystem"]
```

Agent-level identity (ADR-0014) lives in the agent context: `alias` (display name) and `role` (purpose). The scaffold default profile is `orchestrator`, containing a single `jvagent/orchestrator` action (plus `openai_lm`, `reply`, `intro`, `handoff`). Scaffold with `jvagent app create --profile orchestrator` (the default); see the reference agent at `examples/jvagent_app/agents/jvagent/orchestrator_agent/`.

## Module structure

```
jvagent/action/orchestrator/
  ├─ orchestrator_interact_action.py  # orchestrator: walk-path curation + tool-surface assembly + loop
  ├─ continuation.py                     # active-flow surfacing (active_flow_owner + active_flow_note)
  ├─ tools.py                            # SkillTool primitives + wrap/parse/render helpers
  ├─ core_tools.py                       # built-in orchestrator core tools
  ├─ catalog.py                          # tool catalog (find_tool/load_tool) + lean surfacing
  ├─ skills.py                           # skill discovery (JV + Claude specs) + find_skill/use_skill
  ├─ prompts.py                          # orchestrator + loop prompts
  ├─ access.py                           # tool:* / tool:delegate AC
  └─ info.yaml                           # package metadata
```

## Skills (two specs: JV + Claude)

A skill is **judgment over capability, not capability** (ADR-0011). Tools answer "can I do X"; a skill is a standard operating procedure that *coordinates* the tools the agent already has. The orchestrator manages **exactly two skill specs**, distinguished by a `spec` frontmatter key (ADR-0017) — full authoring reference in [`jvagent/skills/README.md`](../jvagent/skills/README.md):

- **JV skill** (`spec: jv`, default) — a `SKILL.md` body that references action/IA tools by their `namespace__tool` name (via `allowed-tools`/`requires-actions`) and carries **no executable code**. It coordinates existing actions-as-tools. Examples: `research`, `answer`.
- **Claude skill** (`spec: claude`) — a standard [Anthropic Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) folder (drop-in with agentskills.io): `SKILL.md` + bundled `scripts/`/`resources/`. On activation the orchestrator **stages the folder into the caller's per-user sandbox** (`staged_skills/<name>/`) and the model runs its scripts via `code_execution__bash` — the multitenant [`jvagent/code_execution`](../jvagent/action/code_execution) substrate (off by default). Examples: `pdf-generation`, `triage`. The earlier "skill `scripts/` as typed tools" idea was reverted — there is no third spec.

**Sources** — discovery ([`orchestrator/skills.py`](../jvagent/action/orchestrator/skills.py)) reuses the neutral `jvagent.scaffold.skill_resolve` over two locations, selected by `skills_source`:

| `skills_source` | Loads from |
|---|---|
| `app` | adjacent `agents/<ns>/<agent>/skills/*` only |
| `library` | built-in `jvagent/skills/*` only |
| `both` (default) | both, app-local overriding built-in by name |

Aliases `local`→`app` and `builtin`→`library` are accepted; `registry` is retired (treated as `library`).

**Selecting which skills** — `skills` is either `-all` (every discovered skill) or a **finite list of names** (fnmatch patterns) in the descriptor, e.g. `skills: [research, web_lookup]`. `denied_skills` subtracts; a skill with `always-active: true` in its frontmatter loads regardless of the selector.

**Exposure + execution** — the loaded skills (name + description) are listed inline in the system prompt under **AVAILABLE SKILLS** (the skill index is *not* gated by lean tool surfacing — it stays fully shown), and the prompt's first rule is **skills-first**: *if a listed skill matches the user's task, activate it with `use_skill` before any ad-hoc tool call.* This makes skills preferred over tool-only handling rather than only discoverable on demand. The orchestrator also adds `find_skill` / `use_skill` meta-tools: `find_skill` searches names+descriptions (for larger catalogs); `use_skill` activates one by name. What activation does depends on the spec:

- **JV skill** — returns the SOP body as an observation (persisting for the rest of the loop) **and surfaces the skill's `allowed-tools` into the loop's callable set**, so the model can immediately invoke the tools the procedure names.
- **Claude skill** — additionally **stages the skill folder** into the caller's per-user sandbox (`staged_skills/<name>/`) and appends a note telling the model to run its scripts via `code_execution__bash`; the model then reads bundled files / runs scripts (Anthropic level-3 disclosure) as needed.

`use_skill` is **idempotent** (re-activating an already-active skill returns a short "proceed" directive instead of re-dumping the SOP), and a loop **repeat-guard** breaks any tool that's called repeatedly with identical args. `allowed-tools` is a **soft dependency** — a skill still activates if a referenced tool is absent, but the activation observation warns so the model won't follow an unexecutable step. `requires-actions` is a **hard gate** — a skill whose declared Action types don't all resolve (enabled) on the agent is **hidden** for that turn: dropped from the surfaced list, `find_skill`, `use_skill`, and `always-active` pinning, so the model never sees a skill it can't run. Each entry may carry an optional **inline version constraint** (PEP 508-style, the comparison operator is the delimiter): `PageIndexAction>=2.0`, `WebFetchAction==1.4.0`, `GmailAction>=1.0,<2.0`; the resolved Action's `get_version()` is checked against it (fails closed when constrained but the action reports no/uncomparable version). This subsumes the removed `requires-action-versions` map.

```yaml
actions:
  - action: jvagent/orchestrator
    context:
      skills_source: both          # app | library | both
      skills: [research, web_lookup]   # finite list, or "-all"
      denied_skills: []
```

## Known follow-ups

- Both skill specs are wired: JV skills (SOP coordinating actions-as-tools) and **Claude skills** (`SKILL.md` + bundled scripts run in the multitenant `code_execution` sandbox, ADR-0017). The subprocess executor backend is a pragmatic default, not a hard jail — untrusted/third-party skills want an isolating backend (container/bubblewrap/nsjail); object-storage sandboxes need a materialize/sync layer (local file storage only for now).
- First-entry routing accuracy now depends on model tool-selection (anchors-in-description + a routing nudge + tests mitigate this); trivial-turn latency, since every non-flow turn enters the loop (mitigated by lean tool surfacing — ADR-0018 — and a `converse` fast-reply skill). Both measured at rollout.
- Live-provider smoke + a performance ledger entry (the in-tree smoke mocks leaf model calls).
