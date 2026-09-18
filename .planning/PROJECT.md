# jvagent — Project Vision

> **Status**: Draft, AI-agent-maintained. Last review: 2026-09-17.
> **Companion docs**: [`SPEC.md`](SPEC.md) for normative semantics, [`architecture.md`](architecture.md) for diagrams, [`../README.md`](../README.md) for user-facing onboarding.
> **Current milestone**: [`ROADMAP.md`](ROADMAP.md) — v2.0 Harness Excellence.

## TL;DR

**jvagent** is a modular AI-agent platform built on [jvspatial](reference/jvspatial-integration.md)'s object-spatial graph primitives. An *app* declares one or more *agents* in YAML; each agent owns a graph of typed *actions* (plugins) and a per-user memory subgraph (`User → Conversation → Interaction`). Incoming traffic becomes an `Interaction`, an `InteractWalker` traverses the agent's `InteractAction` chain, and the **Orchestrator** action — the single orchestrator at weight `-200` — runs the whole turn inside one `execute()`: a deterministic continuation check (resume an active flow), then a bounded think-act-observe loop over a **unified tool surface** where routing is tool selection (no recruited "centers"). Egress is voiced by `ReplyAction` from the agent's identity.

jvagent targets **both** short turn-based conversations (chatbots, channel adapters) **and** long-running autonomous agents (multi-step task plans, scheduled background work). The runtime favors real deployments over toy demos: namespaced plugins, deep action lifecycle hooks, distributed locking, bounded pruning latency, and probabilistic cache cleanup tuned for serverless workers.

---

## Purpose

Build, deploy, and maintain production AI agents without re-implementing the boring parts every time. Hand the engineer a declarative `app.yaml` + `agent.yaml`, a plugin contract, and a runtime that already solves:

- Per-user memory with bidirectional interaction chaining and rolling-window pruning.
- A response bus that adapts the same generated reply for WhatsApp, Messenger, email, web SSE, or stdout.
- A walker pipeline that lets the **model** decide which tools to call rather than the developer hardcoding a control flow.
- HTTP endpoints, auth, file storage, observability, structured logging, and graph repair — out of the box.

The model is the pilot. Tools are the controls. Skills are the flight plan. ([source](../docs/ORCHESTRATOR.md))

---

## Current Milestone: v2.0 Harness Excellence

**Goal:** Make jvagent the dependable, graph-native harness for many agents, users, and simultaneous sessions — without sacrificing model agency, Claude-skill compatibility, or host-neutrality.

**North star:** [`docs/HARNESS_EXCELLENCE_PLAN.md`](../docs/HARNESS_EXCELLENCE_PLAN.md)

**Target features:**
- Native identity `(agent_id, user_id, session_id)` as the only isolation key in core
- Immutable `ToolSurfaceSnapshot` for tools and skills; no cross-session cache leakage
- Graph-backed `TurnRun` journal, invocation ledger, and durable event outbox
- Host-neutral `HostCapabilityProvider` with embedded and remote adapters
- Signed skill manifests and selectable isolation backends
- Correlated trace/replay, load evidence, and a release compatibility matrix

**Requirements:** [`REQUIREMENTS.md`](REQUIREMENTS.md) · **Roadmap:** [`ROADMAP.md`](ROADMAP.md) · **State:** [`STATE.md`](STATE.md)

---

## Target workloads

### 1. Turn-based conversational agents
- Synchronous request/response over HTTP (`POST /agents/{id}/interact`).
- Streaming SSE or one-shot JSON.
- Channel adapters fan the same response out to WhatsApp / Messenger / email / web.
- Memory pruning keeps long-running conversations bounded.
- Example: customer-support bots, sales-qualification bots, smalltalk + skill-routing assistants.

### 2. Long-running autonomous agents
- Tasks created mid-conversation persist on the `Conversation`/`Interaction` node and survive process restarts.
- `run_in_background` `InteractAction`s execute *after* the user response is sent, isolating slow work from request latency.
- The Orchestrator's think-act-observe loop (one model call per tick, per-turn observations + activation budget) re-derives the per-step concerns — stream commits, access control, recording — at loop level inside a single `execute()` call.
- Example: research agents, multi-step task executors, scheduled outreach.

---

## Non-goals

- **Not a model trainer.** jvagent integrates language models; it does not fine-tune them.
- **Not a chat UI.** A reference client (`jvchat/`) exists in-repo for development, but production UIs are out of scope.
- **Not a workflow engine.** No DAG editor, no visual builder. The walker is the workflow; the model is the planner.
- **Not opinionated about persistence.** jvspatial provides the storage; jvagent does not pick a backend for the user. JSON / SQLite / MongoDB / DynamoDB are all first-class.
- **Not a sandboxing layer.** Action authors are trusted. Untrusted code goes in the MCP sandbox or stays out.

---

## Key design tradeoffs

| Choice | Tradeoff |
|---|---|
| **Graph-native state** over relational schema | Easier traversal, walker semantics, plugin extension; harder ad-hoc analytics. Mitigated by separate logging DB. See [`adr/0001`](adr/0001-graph-based-state.md). |
| **Executive frame-stack loop** over walker-revisit | The Executive runs the whole turn in one `execute()` call, re-deriving per-step concerns (access control, streaming, recording) at loop level — no per-iteration graph-traversal overhead. See [`adr/0010`](adr/0010-executive-centers-architecture.md). |
| **Rolling-window pruning** with bounded work per append | Predictable latency over completeness — `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` caps each prune; the rest happens on later appends. See [`adr/0003`](adr/0003-interaction-limit-pruning.md). |
| **Namespace-isolated plugins** (`jvagent/`, `contrib/`, `custom/`) | Avoids name collisions across third-party action packages; adds a layer of indirection in action references. See [`adr/0004`](adr/0004-namespace-isolation.md). |
| **`app.yaml` + `agent.yaml` split** with `run` / `merge` / `source` update modes | Lets ops change config at runtime without redeploying; introduces precedence rules the user must learn. See [`adr/0005`](adr/0005-app-yaml-agent-yaml-split.md). |
| **Separate `jvspatial` framework** rather than embedding the graph layer | jvspatial is reusable outside jvagent and has its own [`SPEC`](../../jvspatial/SPEC.md); jvagent must track its version. See [`adr/0006`](adr/0006-jvspatial-dependency.md). |
| **Pydantic + `@attribute`** for action config | Type safety + agent.yaml override surface; learning curve over plain class attributes. |
| **Executive grants the model full agency** | Maximum flexibility for agent authors and skill writers; harder to predict cost/latency without `activation_budget` + per-tick `ModelBudget`. |

---

## High-level architecture

```
                              jvagent process
   ┌───────────────────────────────────────────────────────────┐
   │  FastAPI server (jvspatial.api.Server)                    │
   │                                                           │
   │  POST /agents/{id}/interact  → InteractWalker             │
   │                                                           │
   │  Graph (jvspatial Nodes + Edges)                          │
   │  Root ─ App ─ Agents ─ Agent ─┬─ Actions ─ Action(s)      │
   │                               │            └─ InteractAction(s)
   │                               └─ Memory ─ User ─ Conversation ─ Interaction*
   │                                                           │
   │  Persistence: JSON │ SQLite │ MongoDB │ DynamoDB           │
   └───────────────────────────────────────────────────────────┘
```

See [`architecture.md`](architecture.md) for full diagrams.

---

## Why graph-based?

Three operational reasons:

1. **Plugin extension is free.** Authors connect new nodes (sub-`InteractAction`s, child caches, tasks) to existing ones — no schema migration.
2. **Walker traversal == control flow.** The walker visits nodes in graph order; routing logic lives on the action rather than in a central dispatcher.
3. **Per-user state is local.** A user's entire history is a connected subgraph; cascading delete is one edge-walk; isolation is by construction.

---

## Repository scope

This repo is `jvagent` only. The graph framework is at `../jvspatial` (sibling dir, pip-installed). See [`jvspatial-integration.md`](reference/jvspatial-integration.md) for the boundary.

---

## Roadmap

- **v1 Orchestrator** — shipped. Historical plan: [`archive/EXECUTIVE-ROADMAP.md`](archive/EXECUTIVE-ROADMAP.md).
- **v2.0 Harness Excellence** — active. [`ROADMAP.md`](ROADMAP.md), sourced from [`docs/HARNESS_EXCELLENCE_PLAN.md`](../docs/HARNESS_EXCELLENCE_PLAN.md).

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Native identity only — no workspace/org/App in core | Hosts map their scopes to session ids; jvagent stays reusable | — Pending v2.0 |
| Snapshots, never live host imports into the Orchestrator | Prevents cache leakage and host-domain coupling | — Pending v2.0 |
| Authority bound server-side, never in model payloads | Model-generated JSON cannot escalate capability | — Pending v2.0 |
| Subprocess limits are development-only containment | Not a sandbox for untrusted code | — Pending v2.0 |
| Single-process remains a documented narrower profile | Active-active is optional, not required | — Pending v2.0 |
| HP-08 and HP-09 run in parallel after HP-03 + HP-05 | Skill hardening does not wait on host transport | — Pending v2.0 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-17 after starting milestone v2.0 Harness Excellence*

---

## Success criteria for the documentation effort

This document and its siblings are *agent-maintained*. They succeed when:

- A fresh AI agent dropped into the repo can answer *"what is jvagent for?"* by reading this file alone.
- A fresh AI agent dropped into a subsystem (`jvagent/core/`, `jvagent/action/orchestrator/`, etc.) can do correct local work by reading the local `CLAUDE.md` alone.
- Every claim about runtime behavior in [`SPEC.md`](SPEC.md) cites a file:line in the source.
- Every load-bearing design decision is captured in an [`adr/`](adr/).
