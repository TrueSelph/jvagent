# jvagent Architecture

> Diagrams + flow walkthroughs. Normative semantics live in [`SPEC.md`](SPEC.md); this doc is the visual companion.

---

## 1. Graph hierarchy

```mermaid
graph TD
    Root[Root<br/><i>jvspatial singleton</i>]
    App[App<br/>core/app.py:19]
    Agents[Agents<br/>core/agents.py:17]
    Agent[Agent<br/>core/agent.py:18]
    Actions[Actions<br/>action/actions.py]
    Action1[Action<br/>action/base.py:48]
    IA1[InteractAction<br/>action/interact/base.py:32]
    IAChild[Sub-InteractAction<br/>conditionally routed]
    Memory[Memory<br/>memory/manager.py:18]
    User[User<br/>memory/user.py:25]
    Conv[Conversation<br/>memory/conversation.py:39]
    I1[Interaction]
    I2[Interaction]
    I3[Interaction]

    Root --> App
    App --> Agents
    Agents --> Agent
    Agent --> Actions
    Agent --> Memory
    Actions --> Action1
    Actions --> IA1
    IA1 -. visit .-> IAChild
    Memory --> User
    User --> Conv
    Conv --> I1
    I1 <--> I2
    I2 <--> I3
```

**Edge semantics**
- Solid arrows: structural edges (`Root → App`, `Agent → Actions`, etc.).
- `Conv → I1` is `direction="out"`. `I1 ↔ I2` (and onward) are `direction="both"` after the second `Interaction` lands ([`conversation.py:268-272`](../jvagent/memory/conversation.py)).
- `IA1 -. visit .-> IAChild` is a runtime walker hop, NOT a persisted edge from the parent's `execute()` — top-level `InteractAction`s explicitly route the walker into their children ([`interact/base.py:47-54`](../jvagent/action/interact/base.py)).

---

## 2. Boot sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as User shell
    participant M as __main__.py
    participant CLI as cli/main.py
    participant CFG as cli/server_config.py
    participant BOOT as cli/bootstrap.py
    participant SRV as jvspatial Server (FastAPI)
    participant UV as uvicorn

    U->>M: python -m jvagent [app_root]
    M->>CLI: main()
    CLI->>CLI: _first_app_root_path(args)
    CLI->>CLI: load_app_env(app_root)
    CLI->>CLI: set_app_root(app_root)
    CLI->>CLI: reload_performance_config + profiling
    CLI->>CLI: parse --serverless / --debug / --update / --source / --merge
    alt Subcommand
        CLI->>CLI: dispatch (status/agent/action/bootstrap/...)
    else Default — run server
        CLI->>CFG: create_server_from_config(app_root, update_mode)
        CFG->>BOOT: bootstrap_application_graph()
        BOOT->>BOOT: App.get() | load app.yaml
        BOOT->>BOOT: per-agent: agent.yaml → Agent + Actions
        BOOT->>SRV: register core endpoints + action @endpoint routes
        CFG->>UV: uvicorn.run(server, host, port)
    end
```

References:
- `cli/main.py:118-244` — entry
- `cli/server_config.py:59-180` — `create_server_from_config`
- `cli/bootstrap.py` — `bootstrap_application_graph()`
- `core/app_loader.py` — `app.yaml → App`
- `core/agent_loader.py` — `agent.yaml → Agent`

---

## 3. Interaction flow — `POST /agents/{id}/interact`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant FE as FastAPI endpoint<br/>(action/interact/endpoints.py:174+)
    participant W as InteractWalker<br/>(action/interact/interact_walker.py)
    participant MEM as Memory + Conversation
    participant IA as InteractAction(s)<br/>weight-ordered
    participant RB as ResponseBus
    participant BG as Background queue

    C->>FE: POST /agents/{id}/interact { utterance, channel, session_id, ... }
    FE->>W: InteractWalker(payload).spawn(agent)
    W->>MEM: _bootstrap_interaction()
    MEM->>MEM: resolve/create User (lock_manager)
    MEM->>MEM: resolve/create Conversation by session_id
    MEM->>MEM: append Interaction (chain edges; check interaction_limit; prune if over)
    loop For each top-level InteractAction in weight order
        W->>IA: on_interact_action() → execute(walker)
        alt run_in_background == True
            W->>BG: queue (don't execute inline)
        else inline
            IA->>RB: publish(...) or respond(...) via PersonaAction
            IA->>W: optionally visitor.visit(child)
        end
    end
    W->>FE: build_interact_response()
    alt streaming
        FE-->>C: SSE stream chunks
    else single response
        FE-->>C: JSON response
    end
    Note over FE,BG: After response sent
    FE->>BG: _run_background_actions(walker)
    BG->>IA: execute() each (try/except isolated)
```

References:
- `action/interact/endpoints.py:174+` — endpoint
- `action/interact/endpoints.py:65-109` — background runner
- `action/interact/interact_walker.py:50-800` — walker logic
- `action/interact/interact_walker.py:277-450` — bootstrap
- `memory/conversation.py:250-367` — append + prune

---

## 4. SkillExecutive turn (continuation check + think-act-observe loop)

```mermaid
sequenceDiagram
    autonumber
    participant W as InteractWalker
    participant SE as SkillExecutiveInteractAction
    participant CC as Continuation check (deterministic)
    participant FL as Active flow (e.g. interview)
    participant M as LanguageModel
    participant T as Tool surface
    participant PER as Persona reply/respond

    W->>SE: execute(walker)
    SE->>CC: active flow-task in TaskStore?
    alt yes
        CC->>FL: flow.resume(visitor)
        FL-->>SE: ACTIVE  → end turn
        FL-->>SE: COMPLETE → clear task, fall through ↓
        FL-->>SE: YIELD    → fall through ↓ (re-enter loop)
    else no
        CC-->>SE: fall through ↓
    end
    loop think-act-observe (one model call per tick, bounded)
        SE->>M: model tick over the unified tool surface
        M-->>SE: select a tool (routing = tool selection)
        SE->>T: dispatch tool (AC-gated): IA-as-tool · action tool ·\ncore tool · find_skill/use_skill · find_tool/load_tool
        T-->>SE: observation
    end
    SE->>PER: reply / respond (model-discretionary egress)
    PER-->>W: published; execute() returns once
```

**Why one `execute()` call instead of walker-revisit?** The SkillExecutive re-derives the per-step concerns — one model call per tick, access control, observability, runaway bound — at loop level inside a single `execute()`, so the turn completes without graph-traversal overhead per iteration. The continuation check reads persisted state only (no model); resume is never re-decided by the model. See [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md) (supersedes ADR-0010).

References:
- `action/skill_executive/skill_executive_interact_action.py` — orchestrator: walk-path curation + tool-surface assembly + loop
- `action/skill_executive/continuation.py` — active-flow surfacing (`active_flow_owner` + `active_flow_note`)
- `action/skill_executive/tools.py` — SkillTool primitives + helpers
- `action/interact/base.py` — `InteractAction.get_tools()` (an IA furnishes its own tool, forwarding to `execute`)
- `action/persona/persona_action.py` — `PersonaAction.get_tools()` (reply/respond, visitor-bound inline via `wrap_action_tool`)
- `action/skill_executive/core_tools.py` — built-in core tools
- `action/skill_executive/catalog.py` / `skills.py` — find_tool/load_tool and find_skill/use_skill

---

## 5. Memory pruning

```mermaid
flowchart TD
    A[Interaction appended] --> B[Conversation.add_interaction]
    B --> C{interaction_limit > 0?}
    C -- no --> Z[done]
    C -- yes --> D{count > limit?}
    D -- no --> Z
    D -- yes --> E[_prune_old_interactions]
    E --> F[to_remove = count - limit]
    F --> G[max_prune = min<br/>JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL]
    G --> H[walk from first<br/>delete oldest, rewire edges<br/>stop if next is None]
    H --> I[update last_interaction_id]
    I --> Z
```

References:
- `memory/conversation.py:289-367` — full pruning routine
- `memory/conversation.py:317-323` — env-bounded cap
- `memory/conversation.py:333-336` — never-delete-last invariant

---

## 6. Response emission

```
InteractAction.publish() ─┬─ visitor.response_bus.publish(...)
                          │
                          ├─ stream=True (default) → SSE flush
                          ├─ stream=False          → adhoc single message
                          ├─ category=user|thought → ResponseMessage type
                          └─ relay_to_adapters     → channel adapters fan-out
                                                      (WhatsApp, Messenger, Email, SSE, ...)
```

- Source: [`action/interact/base.py:193-274`](../jvagent/action/interact/base.py).
- Channel adapters: `action/response/channel_adapter.py`.
- Filters: `action/response/channel_filter.py`.
- Per-agent bus: `Agent.get_response_bus()` ([`agent.py:211`](../jvagent/core/agent.py)).

### 6.1 Proactive (out-of-walker) sends

```
Agent.send_proactive_message(user_id, content, channel, ...)
   │
   ├─ Memory.get_user(user_id, create_if_missing=True)
   ├─ user.get_conversation_by_session(session_id) OR get_active_conversation() OR create_conversation()
   ├─ conversation.add_interaction(utterance="")     ── empty utterance = proactive marker
   ├─ interaction.add_parameter({"is_proactive": True, ...metadata}, source_action)
   └─ response_bus.publish(category="user", interaction=..., content=..., channel=...)
         │
         ├─ channel adapter dispatch (e.g. WhatsAppAdapter.send → provider API)
         └─ _append_to_interaction_response_impl → interaction.set_response + save
```

- Source: [`agent.py:226-319`](../jvagent/core/agent.py).
- Empty-utterance entries are suppressed from the `role: "user"` slot in LLM history at [`conversation.py:553-566`](../jvagent/memory/conversation.py); they render as standalone `assistant` turns.
- User-facing reference: [`../docs/proactive-messages.md`](../docs/proactive-messages.md).

---

## 7. Logical layers

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI (jvagent/cli/) — entry, subcommands, server bootstrap      │
├─────────────────────────────────────────────────────────────────┤
│  Core (jvagent/core/) — App/Agent nodes, config, repair         │
├─────────────────────────────────────────────────────────────────┤
│  Memory (jvagent/memory/) — User/Conversation/Interaction       │
├─────────────────────────────────────────────────────────────────┤
│  Action library (jvagent/action/) — plugins                     │
│    ├─ interact/  — base + walker + endpoints                    │
│    ├─ skill_executive/ — SkillExecutive orchestrator + tools    │
│    ├─ model/     — LLM + embedding actions                      │
│    ├─ response/  — bus + channel adapters                       │
│    └─ <vertical actions>: google, microsoft, whatsapp, email... │
├─────────────────────────────────────────────────────────────────┤
│  Logging (jvagent/logging/) — separate logs DB + query endpoints│
├─────────────────────────────────────────────────────────────────┤
│  Scaffolding (jvagent/scaffold/) — `jvagent app create` flow    │
├─────────────────────────────────────────────────────────────────┤
│  jvspatial (sibling pkg) — Object/Node/Edge/Walker, FastAPI,    │
│                            JSON/SQLite/MongoDB/DynamoDB         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. Where data lives

| Concern | Storage |
|---|---|
| Graph nodes | jvspatial-managed DB (JSON / SQLite / MongoDB / DynamoDB; configurable per env) |
| Logs | **Separate** `logs` DB via `get_logging_service(database_name="logs")` (`logging/endpoints.py:39+`) |
| Files | `App.file_storage_provider` — local FS or S3 (`core/app.py:49`) |
| Locks | In-process per-event-loop (`core/app.py:94-95`); jvspatial may add distributed locks |

---

## 9. Where to look next

| If you want to... | Read |
|---|---|
| Build an action | [`action-authoring.md`](reference/action-authoring.md) |
| Understand the SkillExecutive deeply | [`../docs/EXECUTIVE.md`](../docs/EXECUTIVE.md) + [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md) |
| Tune logging | [`observability.md`](reference/observability.md) |
| Run locally | [`runbooks/local-dev.md`](runbooks/local-dev.md) |
| See every action | [`actions-catalog.md`](reference/actions-catalog.md) |
| Understand jvspatial | [`jvspatial-integration.md`](reference/jvspatial-integration.md) |
