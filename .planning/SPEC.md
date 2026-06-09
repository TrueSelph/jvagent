# jvagent Specification

> **Normative.** This document defines what jvagent guarantees, what nodes and walkers contract with each other, and what behavior implementations must preserve. Every claim cites a file:line.
>
> For terminology, see [`GLOSSARY.md`](GLOSSARY.md). For diagrams, see [`architecture.md`](architecture.md). For the underlying graph framework, see [`jvspatial-integration.md`](reference/jvspatial-integration.md).

---

## 1. Scope

jvagent specifies:

1. The **graph hierarchy** of an application and the invariants between nodes.
2. The **interaction lifecycle** — request bootstrap, walker traversal, response emission, background tasks.
3. The **action contract** — base classes, lifecycle hooks, attribute config, weight ordering.
4. The **memory contract** — User / Conversation / Interaction structure and rolling-window pruning.
5. The **configuration resolution chain** — env → app.yaml → agent.yaml → defaults.
6. The **bootstrap modes** — `run`, `merge`, `source`.
7. The **invariants** an implementer must not break.

It does **not** specify: choice of model provider, transport protocol details (HTTP semantics are jvspatial's), storage backend selection, deployment topology, or the Orchestrator's prompt design (see [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md)).

---

## 2. Graph hierarchy

```
Root (jvspatial)
 └── App
      └── Agents
           └── Agent  (1..N)
                ├── Actions
                │    └── Action  (1..N, weight-ordered when InteractAction)
                │         └── (sub-InteractAction children, optional)
                └── Memory
                     └── User  (1..N, unique per (memory_id, user_id))
                          └── Conversation  (1..N per user, session_id-keyed)
                               └── Interaction  (1..N, bidirectionally chained)
```

| Node | File | Purpose |
|---|---|---|
| `Root` | jvspatial `core/entities/root.py` | jvspatial singleton, anchor for everything. |
| `App` | [`jvagent/core/app.py:19`](../jvagent/core/app.py) | Root application node; singleton via `App.get()` cache ([`app.py:124`](../jvagent/core/app.py)). |
| `Agents` | `jvagent/core/agents.py:17` | Structural branchpoint; aggregates agent stats. |
| `Agent` | [`jvagent/core/agent.py:18`](../jvagent/core/agent.py) | One agent; cached fetch via `Agent.get(agent_id)` ([`agent.py:63`](../jvagent/core/agent.py)). |
| `Actions` | `jvagent/action/actions.py` | Branchpoint for an agent's action set. |
| `Action` | [`jvagent/action/base.py:48`](../jvagent/action/base.py) | Base for all action plugins. |
| `InteractAction` | [`jvagent/action/interact/base.py:32`](../jvagent/action/interact/base.py) | Subclass of `Action` participating in the interact pipeline. |
| `Memory` | `jvagent/memory/manager.py:18` | Branchpoint for an agent's per-user state. |
| `User` | `jvagent/memory/user.py:25` | Identity scoped by `(memory_id, user_id)`; compound index enforced. |
| `Conversation` | `jvagent/memory/conversation.py:39` | Session-keyed conversation; chained `Interaction`s. |
| `Interaction` | `jvagent/memory/interaction.py:47` | Single exchange; bidirectional edges to prior/next. |

### 2.1 Singleton invariants

- **Exactly one `App` per process** ([`app.py:88`](../jvagent/core/app.py): `_cached_app` class var, `_get_lock` per-event-loop lock for serverless safety).
- **Exactly one `Agents` and one `Memory` per agent** (implied by the `await agent.node(node="Actions")` / `await agent.node(node="Memory")` lookups at [`agent.py:95`](../jvagent/core/agent.py), [`agent.py:179`](../jvagent/core/agent.py)).
- **Exactly one `User` per `(memory_id, user_id)`** (compound index at `memory/user.py:16-24`; lock manager at `memory/lock_manager.py` enforces concurrent-create safety).
- **Exactly one `Conversation` per `session_id`** per user (enforced at `Memory.get_user()` + conversation lookup).

### 2.2 Edge contract

- `Conversation → Interaction` edges are **directional** at first (`direction="out"` at [`conversation.py:272`](../jvagent/memory/conversation.py)) and become **bidirectional** when the next `Interaction` is appended ([`conversation.py:270`](../jvagent/memory/conversation.py): `await last_interaction.connect(interaction, direction="both")`).
- An `Action`'s outgoing edges to child nodes are **cascade-deleted** when the action is deleted ([`base.py:225`](../jvagent/action/base.py)). Implementations that attach child state (e.g., caches) MUST connect via outgoing edges.

---

## 3. Interaction lifecycle

### 3.1 Request → response sequence

1. **HTTP entry**: `POST /agents/{agent_id}/interact` is registered in `jvagent/action/interact/endpoints.py:174+`.
2. **Walker spawn**: `InteractWalker` is constructed with payload, then spawned on the `Agent` node. Walker source: `jvagent/action/interact/interact_walker.py:50+`.
3. **Bootstrap**: `InteractWalker._bootstrap_interaction()` (`interact_walker.py:277-450`) resolves or creates `User`, `Conversation`, and `Interaction`.
4. **Visit InteractActions**: walker visits each top-level `InteractAction` connected to the agent's `Actions` node, **in ascending `weight` order** ([`interact/base.py:64`](../jvagent/action/interact/base.py)).
5. **Execute**: walker's `on_interact_action()` callback calls `await action.execute(walker)` if routing checks pass. Background actions are queued, not executed inline ([`endpoints.py:65-109`](../jvagent/action/interact/endpoints.py)).
6. **Response build**: `build_interact_response()` constructs JSON or SSE stream.
7. **Post-response background**: `_run_background_actions(walker)` fires the queued `run_in_background` actions as fire-and-forget asyncio tasks. Each is isolated in try/except — failures do not block others.

### 3.2 Walker contract

The `InteractWalker` MUST:
- Initialize `walker.interaction`, `walker.conversation`, `walker.user_id`, `walker.session_id`, `walker.channel`, `walker.response_bus`, `walker.stream` before visiting any `InteractAction`. There is no `walker.user` (the full User node); use `Memory.get_user(walker.user_id)` when the node is needed.
- Visit top-level `InteractAction`s in ascending `weight` order. Sub-`InteractAction`s connected as children are traversed only via explicit `visitor.visit()` calls from the parent's `execute()` ([`interact/base.py:47-54`](../jvagent/action/interact/base.py)).
- Respect `run_in_background=True`: defer to the background queue, do not execute inline ([`interact/base.py:88`](../jvagent/action/interact/base.py)).
- Honor `always_execute=True`: bypass routing exclusion for that action ([`interact/base.py:78`](../jvagent/action/interact/base.py)).
- Enforce access control at each visit (`enforce_interact_action_access()` at `interact_walker.py:231`).

The `InteractAction.execute()` contract:
- Receives the walker; reads state via `walker.interaction`, `walker.conversation`, `walker.user_id` (no `walker.user` field exists).
- MUST perform its own evaluation checks at the start and return early if not applicable ([`interact/base.py:147-191`](../jvagent/action/interact/base.py)).
- Top-level actions with child `InteractAction`s MUST explicitly route via `visitor.visit(child)` — the walker does not auto-traverse.
- MAY emit responses via `await self.publish(...)`, `self.publish_thought(...)`, or `self.respond(...)`.
- MUST NOT block the loop; long operations belong in `run_in_background` actions or in tasks.

### 3.3 Orchestrator pattern

`OrchestratorInteractAction` ([`orchestrator/orchestrator_interact_action.py`](../jvagent/action/orchestrator/orchestrator_interact_action.py)) is the sole pattern orchestrator at `weight=-200`. It does **not** use walker-revisit: it runs the whole turn inside one `execute()` call and returns once. There are no recruited centers, no deterministic reflex, and no separate router or capability registry. A turn proceeds in two stages:

1. **Think-act-observe loop** (one model call per tick, bounded) — over a unified tool surface, the model selects tools until the turn is answered. **Routing is tool selection** (ADR-0012).
2. **Turn-lock (a restriction on that surface)** — the orchestrator detects an active flow via `continuation.active_flow_owner(visitor)` (a deterministic read of the active control-task's `owner_action`, equal to the IA's tool name). When `lock_active_flow` is on (default) and a flow is active, the loop restricts its callable surface to that IA's tool and dispatches it with no model round-trip. When off, it instead makes the flow's tool visible and injects `continuation.active_flow_note(tool_name)`, leaving continuation to the model.

**Flow continuation mode is configurable** via `lock_active_flow` ([ADR-0013](adr/0013-togglable-deterministic-turn-lock.md); [`orchestrator/continuation.py`](../jvagent/action/orchestrator/continuation.py) exposes `active_flow_owner(visitor)` and `active_flow_note(tool_name)`):

- **`lock_active_flow=True` (default) — deterministic turn-lock.** `_run_loop` restricts the callable surface to the owning IA's tool and dispatches it (the same visitor-bound, AC-gated, terminal `wrap_action_tool` binding used for routing) with no model round-trip. The flow owns every turn until it clears its own task; off-topic input goes into the IA, which owns interruption/cancel.
- **`lock_active_flow=False` — model-mediated.** The note reads roughly *"a multi-step flow is in progress; call `<tool>` to continue it if the user is engaging, otherwise handle their request normally — the flow stays active and resumes when the user returns."* The model then either **continues** (selects the flow's tool → `get_tools` → `execute` advances the session) or **routes elsewhere** for an off-topic utterance (the flow is not forced to run; this prevents the "Who is Eldon Marks?" misroute, and interruptibility is automatic — no `can_interrupt` branch).

In both modes a flow's only orchestrator-facing modification is being exposed via `get_tools()` (forwarding to `execute(visitor)`); it gains no `resume()` method and no orchestrator-specific flags, and its control-task persists across turns until the flow's own session logic clears it.

**Invariants:**

1. **One model call per tick**; the loop is bounded by an activation budget (each tick is at most one model round-trip).
2. **Flow continuation mode is configurable** via `lock_active_flow` ([ADR-0013](adr/0013-togglable-deterministic-turn-lock.md)). Active-flow detection (`active_flow_owner`) is always a deterministic read of persisted `TaskStore` state (no model).
3. **Turn-lock is deterministic when `lock_active_flow=True`** (default — the loop restricts its callable surface to the active flow's IA tool and dispatches it with no model round-trip) and **emergent/model-mediated when `False`** (the flow's tool is surfaced and the model decides whether to continue or detour). In both modes the control-task persists across turns and is cleared only by the flow's own session logic.
4. **Routing is tool selection.** There is no separate router or capability registry; IAs (as tools forwarding to `execute(visitor)`), persona `reply`/`respond`, core services, and skills are all tools. An IA's tool *description* is built from its manifest (`purpose` + `activates_on`, via `routing_triggers()`) so the model routes on intent.
5. **Actions own their output.** Actions publish their own results; the `reply`/`respond` persona tools are model-discretionary. A turn that ends with no emission and no active flow gets a single fallback reply.
6. **Access control gates tool dispatch** (`tool:*`), including IA-as-tool execution (`tool:delegate:{name}` preserved).
7. **Walk-path curation.** Because the Orchestrator coexists with the interact pipeline, a routable IA is still a top-level `InteractAction` the walker would execute every turn. Each turn the orchestrator curates the remaining walk path (`visitor.curate_walk_path`) to drop tool-exposed (routable) IAs — reached only by tool selection — keeping itself, `always_execute` IAs, and non-routable IAs.

The tool surface is assembled in [`orchestrator/orchestrator_interact_action.py`](../jvagent/action/orchestrator/orchestrator_interact_action.py); egress tools come from `Action.get_responder()` → `ReplyAction.get_tools()` (preferred) or `PersonaAction.get_tools()` (fallback), each IA furnishes its own tool via `InteractAction.get_tools()` (the orchestrator binds the visitor + AC), and progressive disclosure (`find_tool`/`load_tool`, `find_skill`/`use_skill`) comes from [`orchestrator/catalog.py`](../jvagent/action/orchestrator/catalog.py) and [`orchestrator/skills.py`](../jvagent/action/orchestrator/skills.py).

Rationale and consequences: [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md) (supersedes [`adr/0010-executive-centers-architecture.md`](adr/0010-executive-centers-architecture.md)). Milestones: [`.planning/archive/EXECUTIVE-ROADMAP.md`](archive/EXECUTIVE-ROADMAP.md).

Harness design contract (thin server, thick SOP): [`docs/thin-harness.md`](../docs/thin-harness.md). Interview profile: [`jvagent/action/interview_action/docs/thin-harness.md`](../jvagent/action/interview_action/docs/thin-harness.md).

---

## 4. Action contract

### 4.1 Base class hierarchy

```
Node (jvspatial)
└── Action                     jvagent/action/base.py:48
    ├── BaseModelAction        jvagent/action/model/base.py:26
    │   └── LanguageModelAction  jvagent/action/model/language/base.py:24
    │       ├── AnthropicLanguageModelAction
    │       ├── OpenAILanguageModelAction
    │       ├── OpenRouterLanguageModelAction
    │       └── OllamaLanguageModelAction
    ├── BaseWebSearchAction
    ├── BaseSTTAction, BaseTTSAction, VectorStore (per provider)
    └── InteractAction          jvagent/action/interact/base.py:32
        ├── OrchestratorInteractAction
        ├── InteractRouter
        ├── ConverseInteractAction
        ├── InterviewInteractAction
        └── ...(see actions-catalog.md)
```

### 4.2 Required attributes

Every `Action` MUST persist:
- `agent_id: str` ([`base.py:125`](../jvagent/action/base.py))
- `enabled: bool` ([`base.py:131`](../jvagent/action/base.py))
- `namespace: str` ([`base.py:136`](../jvagent/action/base.py))
- `label: str` ([`base.py:139`](../jvagent/action/base.py))
- `description: str` ([`base.py:144`](../jvagent/action/base.py))
- `metadata: Dict[str, Any]` populated from `info.yaml` ([`base.py:147`](../jvagent/action/base.py))
- `module_path: str` ([`base.py:151`](../jvagent/action/base.py))

`InteractAction` adds:
- `weight: int` (default 0; ordering applies only to top-tier) ([`interact/base.py:64`](../jvagent/action/interact/base.py))
- `always_execute: bool` ([`interact/base.py:78`](../jvagent/action/interact/base.py))
- `run_in_background: bool` ([`interact/base.py:88`](../jvagent/action/interact/base.py))
- `anchors: List[str]` ([`interact/base.py:99`](../jvagent/action/interact/base.py))
- `parameters: List[Dict[str, Any]]` ([`interact/base.py:108`](../jvagent/action/interact/base.py))
- `deny_access_directive: str` ([`interact/base.py:117`](../jvagent/action/interact/base.py))

### 4.3 Lifecycle hooks

Implementations MAY override:

| Hook | When called | Source |
|---|---|---|
| `on_register()` | First-time set-up | [`base.py:256`](../jvagent/action/base.py) |
| `on_reload()` | After update | [`base.py:269`](../jvagent/action/base.py) |
| `post_register()` | After all actions registered (for cross-action wiring) | [`base.py:281`](../jvagent/action/base.py) |
| `on_startup()` | When the action is loaded from DB on app start | [`base.py:307`](../jvagent/action/base.py) |
| `on_enable()` | When transitioned to enabled | [`base.py:294`](../jvagent/action/base.py) |
| `on_disable()` | When transitioned to disabled | [`base.py:321`](../jvagent/action/base.py) |
| `on_deregister()` | When removed from the agent | [`base.py:334`](../jvagent/action/base.py) |
| `pulse()` | Periodic maintenance | [`base.py:544`](../jvagent/action/base.py) |
| `healthcheck()` | Health probe | [`base.py:553`](../jvagent/action/base.py) |

Errors raised by these hooks are logged automatically by the action's `enable()` / `disable()` / `reload()` wrappers ([`base.py:569+`](../jvagent/action/base.py)). Implementations MUST NOT swallow errors silently — let them propagate so the wrapper records them.

### 4.4 Tools and capabilities

- `get_tools() -> List[Tool]` ([`base.py:192`](../jvagent/action/base.py)) — every `Action` MAY expose tools to the agentic loop (e.g. the Orchestrator's think-act-observe loop). Each tool wraps a callable with a JSON Schema for arguments; they are registered with an `action__` prefix in the tool registry. `InteractAction.get_tools()` forwards to `execute(visitor)` and builds the tool description from the manifest (`purpose` + `activates_on`, via `routing_triggers()`).
- `get_capabilities() -> List[str]` ([`base.py:180`](../jvagent/action/base.py)) — short capability strings aggregated by `PersonaAction` for system-prompt injection.

### 4.5 Action discovery

- `Action.get_action(class_or_name, enabled_only=True)` ([`base.py:710`](../jvagent/action/base.py)) is `O(1)` via a cached `class_name → action_id` index maintained at register/deregister time.
- `Action.get_action_by_base_class(base_class)` ([`base.py:766`](../jvagent/action/base.py)) is `O(n)` (isinstance scan).
- `Action.get_model_action(required=False)` ([`base.py:796`](../jvagent/action/base.py)) is the canonical path for actions needing a LLM. Honors a `model_action_type` attribute when present.

### 4.6 Namespace rules

- Format: `namespace/action_name`. Examples: `jvagent/persona`, `contrib/slack`, `custom/my_action`.
- Namespaces prevent name collisions across third-party packages.
- `jvagent/` is reserved for the core library. Third-party publishers SHOULD use a distinct namespace.
- **Canonical name source**: the loader at
  [`jvagent/action/loader/info_yaml.py:42-44`](../jvagent/action/loader/info_yaml.py)
  treats `info.yaml` → `package.name` as authoritative. The directory path
  is a presentational convention that commonly drops a `_action` /
  `_interact_action` suffix for brevity (e.g. `jvagent/whatsapp_action`
  lives in `jvagent/action/whatsapp/`). Tooling and `agent.yaml` must
  reference actions by `package.name`, never by the on-disk directory.

See [`adr/0004-namespace-isolation.md`](adr/0004-namespace-isolation.md) and [`action-authoring.md`](reference/action-authoring.md) for the package layout.

---

## 5. Memory contract

### 5.1 Identity

- `User.memory_id` + `User.user_id` together form a compound unique key per `Memory` subgraph (compound index at `memory/user.py:16-24`).
- A `lock_manager` (`memory/lock_manager.py`) acquires a per-`(memory_id, user_id)` lock before `_get_user_unlocked()` to prevent duplicate `User` rows under concurrent creates.

### 5.2 Conversation chaining

- A new `Interaction` is appended via `Conversation.add_interaction()` ([`conversation.py:199`](../jvagent/memory/conversation.py)).
- The first `Interaction` is connected to the `Conversation` directly with `direction="out"`.
- Subsequent `Interaction`s connect to the previous one with `direction="both"` (bidirectional chain).
- `Conversation.last_interaction_id` is updated atomically with the count and timestamp.
- `Interaction.utterance` defaults to `""` ([`interaction.py:92`](../jvagent/memory/interaction.py)). An empty utterance denotes a **proactive (agent-initiated)** entry — see §7.1 and [`docs/proactive-messages.md`](../docs/proactive-messages.md). `Conversation._format_interactions` ([`conversation.py:553-566`](../jvagent/memory/conversation.py)) skips the `role: "user"` entry when the utterance is empty/whitespace, so proactive entries appear as standalone `assistant` turns in LLM history.

### 5.3 Rolling-window pruning

- The default pruning window is `Agent.interaction_limit` ([`agent.py:50`](../jvagent/core/agent.py); `0` = disabled).
- A `Conversation` may override with its own `interaction_limit`.
- On every `add_interaction()`, if `interaction_count > interaction_limit`, `_prune_old_interactions()` runs ([`conversation.py:289-293`](../jvagent/memory/conversation.py)).
- **Per-call bound**: `_prune_old_interactions` caps removals at `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100, [`conversation.py:317-323`](../jvagent/memory/conversation.py)). Any remaining excess is removed on subsequent appends or by `Memory.apply_interaction_limit_pruning_for_connected_users`.
- Pruning **never** removes the most recent `Interaction` — it stops if there is no next interaction ([`conversation.py:333-336`](../jvagent/memory/conversation.py)).
- After pruning, `last_interaction_id` is reverified and the count decremented.

Rationale: [`adr/0003-interaction-limit-pruning.md`](adr/0003-interaction-limit-pruning.md). User-facing detail: [`memory-and-pruning.md`](reference/memory-and-pruning.md).

### 5.4 What's session-scoped vs cross-session

| Field | Scope |
|---|---|
| `User.memory: Dict[str, str]` | Cross-session; per-user, persistent |
| `User.memory_tags: Dict[str, List[str]]` | Cross-session; tag index |
| `User.user_model` | **Deprecated**; legacy compressed facts |
| `Conversation.context: Dict[str, Any]` | Per-session |
| `Interaction.artifacts` | Per-interaction; pruned with the interaction |
| `Interaction.parameters` | Per-interaction; behavioral guidance |

---

## 6. Configuration resolution

### 6.1 Precedence (highest first)

1. **CLI flag** (`--update`, `--source`, `--merge`, `--purge`, `--debug`, `--serverless`).
2. **Environment variable** (resolved via `jvspatial.env.env`; see [`configuration-keys.md`](reference/configuration-keys.md)).
3. **`app.yaml`** at the app root.
4. **`agent.yaml`** under `agents/`.
5. **Action attribute defaults** declared via `attribute(default=...)`.

### 6.2 Resolution machinery

- `app_loader` ([`jvagent/core/app_loader.py`](../jvagent/core/app_loader.py)) reads `app.yaml` into the `App` node.
- `agent_loader` ([`jvagent/core/agent_loader.py`](../jvagent/core/agent_loader.py)) reads `agent.yaml` into `Agent` nodes and their `Action` children.
- `core/env_resolver.py` expands `${ENV_VAR}` placeholders.
- `core/config.py:59-150` defines `ConfigKey` / `ConfigSchema` precedence.
- agent.yaml `context:` blocks override action attribute defaults via the Pydantic `@attribute` system.

### 6.3 Bootstrap update modes

`App.update_mode` ([`app.py:74`](../jvagent/core/app.py)) controls YAML sync on next start:

| Mode | Behavior |
|---|---|
| `run` (default) | Do not re-sync YAML; use persisted graph as-is. |
| `merge` | Apply a **narrow** merge from `app.yaml` to the `App` node — only `version` and `app_id` are refreshed ([`app_loader.py:308-317`](../jvagent/core/app_loader.py)). Other App-level fields, action installs, and per-agent state are **untouched**. The agent-level loader still runs in `merge` mode for action install/upgrade — see [`agent_loader.py`](../jvagent/core/agent_loader.py). |
| `source` | **Destructive**. YAML is the source of truth; full App fields are overwritten and conflicting graph nodes are reset to YAML state ([`app_loader.py:288-307`](../jvagent/core/app_loader.py)). |

CLI flags `--update` / `--merge` / `--source` override the persisted value for that process only. After a successful `bootstrap` or `run`, the persisted mode is reset to `run` by [`reset_app_update_mode_after_successful_bootstrap()`](../jvagent/core/bootstrap_update_mode.py) (called from `cli/commands.py:577` for `bootstrap` and `cli/server_config.py:556` for `run_server`) so cold restarts do not repeat a one-shot operation. The reset uses [`set_app_update_mode()`](../jvagent/core/app.py) (`object.__setattr__` + `await app.save()`) — `Object.save()` writes the full document via `model_dump`, so the change persists despite `update_mode` being a `protected=True` attribute.

`--source` and `--merge` REQUIRE `--update` ([`cli/main.py:167-172`](../jvagent/cli/main.py)). They are mutually exclusive.

See [`adr/0005-app-yaml-agent-yaml-split.md`](adr/0005-app-yaml-agent-yaml-split.md).

---

## 7. Response bus

The response bus ([`jvagent/action/response/response_bus.py`](../jvagent/action/response/response_bus.py)) is **per-agent**. Each `Agent` lazily constructs one via `Agent.get_response_bus()` ([`agent.py:185`](../jvagent/core/agent.py)).

- Channel adapters (`EmailAction`, `WhatsAppAction`, `FacebookAction`, etc.) register with the bus and translate messages to channel-specific transports.
- Filters can drop, transform, or duplicate messages per channel.
- `InteractAction.publish()` ([`interact/base.py:193`](../jvagent/action/interact/base.py)) is the canonical emit path **from within the walker pipeline**.
- Stream mode defaults to `visitor.stream`; pass `stream=False` for non-streaming publishes.

### 7.1 Proactive (agent-initiated) sends

For messages that originate from code outside an inbound webhook (scheduled outreach, integration callbacks, admin actions), use **`Agent.send_proactive_message(user_id, content, channel, ...)`** ([`agent.py:226-319`](../jvagent/core/agent.py)) — the canonical programmatic entrypoint. It:

- resolves the `User` (via `Memory.get_user(create_if_missing=True)`) and the active `Conversation` (or creates one);
- creates an `Interaction` with `utterance=""` and tags origin under `Interaction.parameters` (`{"is_proactive": True, "action_name": <source_action>, ...metadata}`);
- calls `ResponseBus.publish(category="user", interaction=...)` which dispatches to the channel adapter AND appends `content` to `interaction.response` and saves.

Do NOT publish to the bus directly from outside the walker pipeline — bypassing this method skips the bound `Interaction` and leaves the conversation history incomplete. User-facing reference: [`docs/proactive-messages.md`](../docs/proactive-messages.md).

---

## 8. Logging and observability

- Logging service: [`jvagent/logging/service.py`](../jvagent/logging/service.py) — registers the `INTERACTION` log level.
- HTTP query: `GET /logs/agents/{agent_id}` ([`jvagent/logging/endpoints.py:39-113`](../jvagent/logging/endpoints.py)).
- Logs are stored in a **separate database** named `logs` (jvspatial `get_logging_service(database_name="logs")`).
- `Interaction.observability_metrics` aggregates per-interaction events (model calls, embeddings, tools, errors).
- `Interaction.usage` aggregates token counts and per-model call tallies.

See [`observability.md`](reference/observability.md) for the unified index over `docs/logging.md`, `docs/error-logging.md`, `docs/interaction-logging.md`.

---

## 9. Boot sequence

1. `python -m jvagent` → `jvagent/__main__.py:5` → `jvagent.cli.main.main()` ([`cli/main.py:118`](../jvagent/cli/main.py)).
2. Parse args; extract app root via `_first_app_root_path` ([`cli/main.py:58`](../jvagent/cli/main.py)). Default to `os.getcwd()` if no path token.
3. Load `.env` via `load_app_env(app_root)` ([`cli/main.py:130`](../jvagent/cli/main.py)).
4. Set app root in `core/app_context` ([`cli/main.py:132-134`](../jvagent/cli/main.py)).
5. Reload performance + profiling config from env.
6. Apply `--serverless`, `--debug`, `--update`/`--source`/`--merge` flags.
7. Dispatch:
   - Subcommand (status / agent / action / skill / bootstrap / bundle / app / validate / stress-seed): handled inline.
   - Default: `run_server(update_mode, debug, app_root, stress_seed)` ([`cli/main.py:239`](../jvagent/cli/main.py)).
8. `run_server` → `cli/server_config.create_server_from_config()` → `bootstrap_application_graph()` → uvicorn.

---

## 10. Background execution

`InteractAction.run_in_background=True` defers execution until after the user-facing response is sent. Background actions:

- Are collected by the walker during traversal (`walker.background_actions` list).
- Fire after `await response` resolves, in `_run_background_actions(walker)` ([`endpoints.py:65-109`](../jvagent/action/interact/endpoints.py)).
- Are dispatched as fire-and-forget asyncio tasks. Each is wrapped in try/except — failures do not block siblings.
- Use cases: analytics, model updates, follow-up notifications, task scheduling.

There is **no external task queue** (no Celery / RQ). Long-lived autonomous work uses conversation-embedded `PROACTIVE` tasks (`ProactiveTaskSpec`, `spec_version: 2`) on `Conversation.tasks`, dispatched by `TaskMonitor` (schedule path) or `TaskTriggerInteractAction` (event path) through the full Orchestrator pipeline. See ADR-0022 and [`docs/task-tracking.md`](../docs/task-tracking.md).

---

## 11. Invariants (MUST not break)

1. `App` is a singleton per process. Multiple `App` nodes connected to `Root` is an error state.
2. A `User` is unique per `(memory_id, user_id)`. Bypassing the lock manager can produce duplicates that the compound index will reject.
3. `Conversation._prune_old_interactions()` never deletes the last `Interaction`. Doing so would leave `last_interaction_id` dangling.
4. `InteractAction.execute()` is called by the walker; it MUST NOT recursively invoke the walker on the same action without explicit `visitor.prepend([self])` semantics, or stack overflow / infinite-walk protection (`max_visits_per_node=100` in jvspatial walker) will trip. (The Orchestrator avoids this entirely — it runs its whole turn inside one `execute()` call with no walker-revisit.)
5. `Action.metadata` is the authoritative source for `info.yaml` data. Implementations MUST NOT shadow it.
6. `Agent.interaction_limit = 0` means **disabled**. Implementations MUST NOT prune when limit is `0` or unset.
7. `App.update_mode` MUST reset to `run` after a successful sync; otherwise cold restarts will repeat the merge/source pass.
8. Action endpoints registered via `@endpoint` MUST be discoverable by `_discover_action_endpoints()` ([`base.py:354`](../jvagent/action/base.py)) so deregister can clean them up. Use `/actions/{action_id}/...` path prefixes.
9. The Orchestrator is the single pattern orchestrator at `weight=-200`. It runs at most one model call per tick, bounded by an activation budget. See §3.3.
10. Flow continuation is configurable via `lock_active_flow` ([ADR-0013](adr/0013-togglable-deterministic-turn-lock.md)). When on (default), the active flow's IA tool is dispatched with no model round-trip; when off, the flow is surfaced as routable context and the model decides. See §3.3 invariants 2–3.
11. Routing is tool selection. There is no separate router or capability registry; IAs (as tools), persona, core services, and skills are all tools. A flow's control-task (turn-lock) is persisted on the conversation `TaskStore`; the active flow is surfaced as a routable tool and continued by model tool selection next turn. See §3.3 invariant 4.
12. Access control gates tool dispatch (`tool:*`), including IA-as-tool execution (`tool:delegate:{name}`); a denial routes to the orchestrator's safe-fallback. See §3.3 invariant 6.

---

## 12. Versioning

- jvagent's own version: `jvagent/version.py`.
- Required jvspatial version: `pyproject.toml` line containing `jvspatial>=X.Y.Z`. Current pin: `>=0.0.7`; tested with `0.0.8`. See [`adr/0006-jvspatial-dependency.md`](adr/0006-jvspatial-dependency.md).

---

## 13. Decisions

Load-bearing design choices are captured as ADRs:

- [`adr/0001-graph-based-state.md`](adr/0001-graph-based-state.md)
- [`adr/0003-interaction-limit-pruning.md`](adr/0003-interaction-limit-pruning.md)
- [`adr/0004-namespace-isolation.md`](adr/0004-namespace-isolation.md)
- [`adr/0005-app-yaml-agent-yaml-split.md`](adr/0005-app-yaml-agent-yaml-split.md)
- [`adr/0006-jvspatial-dependency.md`](adr/0006-jvspatial-dependency.md)
- [`adr/0010-executive-centers-architecture.md`](adr/0010-executive-centers-architecture.md) *(superseded by ADR-0012; retained as history)*
- [`adr/0011-skills-two-kinds.md`](adr/0011-skills-two-kinds.md)
- [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md)

---

## 14. Out of scope

- HTTP / streaming / auth wire format → jvspatial.
- Storage backend internals → jvspatial.
- Provider-specific model APIs (Anthropic, OpenAI, etc.) → individual `LanguageModelAction` subclasses.
- The Orchestrator's prompt strategy → [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md) and `jvagent/action/orchestrator/prompts.py`.
