<!-- refreshed: 2026-05-06 -->
# Architecture

**Analysis Date:** 2026-05-06

## System Overview

jvagent is a modular, declarative AI agent platform built on `jvspatial`'s graph-based Node/Edge/Walker primitives. Applications, agents, and actions are defined in YAML descriptors (`app.yaml`, `agent.yaml`, `info.yaml`) and bootstrapped into a persistent object-spatial graph. Agent execution is mediated by a single `InteractWalker` that traverses pluggable `InteractAction` nodes connected to each `Agent`. The `CockpitInteractAction` (current default) gives the language model full agency over harness services and action tools through a think-act-observe loop with walker-revisit iteration.

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         CLI / HTTP Entry Layer                        │
│  `jvagent/cli/main.py`        `jvagent/action/interact/endpoints.py`  │
│  (jvagent / bootstrap /        (POST /interact, /interact/stream      │
│   bundle / agent / skill)       via @endpoint decorators)             │
├──────────────────────────────────────────────────────────────────────┤
│                    Bootstrap / Server Composition                     │
│  AppLoader → AgentLoader → ActionLoader → register_action             │
│  `jvagent/core/app_loader.py`  `jvagent/core/agent_loader.py`         │
│  `jvagent/action/loader/action_loader.py`                             │
├──────────────────────────────────────────────────────────────────────┤
│                    Persistent Graph (jvspatial Nodes)                 │
│  Root → App → Agents ─► Agent ─► Actions ─► Action(s)                 │
│                            └──► Memory ─► User ─► Conversation        │
│                                                  └─► Interaction (↔)  │
├──────────────────────────────────────────────────────────────────────┤
│                       Interaction Execution                           │
│  InteractWalker (Walker) traverses InteractActions in weight order;   │
│  CockpitInteractAction / AgentInteractAction → ToolRegistry +         │
│  ToolExecutionEngine drive the model loop;                            │
│  ResponseBus streams adhoc / final messages to channel adapters.      │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│         Storage (jvspatial pluggable backends + jvagent stores)       │
│  app DB (json | sqlite | mongodb | dynamodb)  · log DB · file storage │
│  PageIndex store · response queues (in-process) · graph repair lock   │
└──────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **CLI dispatcher** | Parse argv, resolve app root, dispatch to subcommands or default server | `jvagent/cli/main.py` |
| **Server config** | Build `jvspatial.api.Server` from `app.yaml` + env, register endpoint modules | `jvagent/cli/server_config.py` |
| **Bootstrap** | Idempotent graph construction: App + Agents + Memory + admin user | `jvagent/cli/bootstrap.py`, `jvagent/core/app_loader.py` |
| **App** | Singleton root application node (id, version, file storage, timezone, update_mode) | `jvagent/core/app.py` |
| **Agents** | Structural branchpoint; aggregate counters over child Agent nodes | `jvagent/core/agents.py` |
| **Agent** | Per-agent identity, configuration, memory + actions hubs, ResponseBus owner | `jvagent/core/agent.py` |
| **Actions** | Central manager for an agent's actions; registration / lookup / counters | `jvagent/action/actions.py` |
| **Action** | Base node for all pluggable actions; lifecycle hooks, get_tools(), get_action() | `jvagent/action/base.py` |
| **InteractAction** | Action subclass that participates in the interact pipeline | `jvagent/action/interact/base.py` |
| **InteractWalker** | Walker that traverses agent → Actions → InteractActions in weight order | `jvagent/action/interact/interact_walker.py` |
| **CockpitInteractAction** | Model-cockpit walker-revisit loop; full harness + action tool agency | `jvagent/action/cockpit/cockpit_interact_action.py` |
| **CockpitEngine** | Single-step think-act-observe iteration; persists state on visitor | `jvagent/action/cockpit/engine.py` |
| **AgentInteractAction** | Legacy unified router + converse + skill loop (single visit) | `jvagent/action/agent_interact/agent_interact_action.py` |
| **InteractRouter** | Posture/intent classifier (CoVe prompting); used by legacy and cockpit | `jvagent/action/router/interact_router.py`, `jvagent/action/cockpit/router.py` |
| **SkillInteractAction** | Interact-subsystem facade over `SkillAction` agentic loop | `jvagent/action/skill/skill_interact_action.py` |
| **PersonaAction** | Tool-based persona / prompt action; converse delivery target | `jvagent/action/persona/persona_action.py` |
| **AccessControlAction** | Per-action gating; consulted by walker before each visit | `jvagent/action/access_control/access_control_action.py` |
| **ResponseBus** | Agent-scoped publish/subscribe for streaming + final messages | `jvagent/action/response/response_bus.py` |
| **Memory** | Manager hub for User/Conversation/Interaction subgraph | `jvagent/memory/manager.py` |
| **TaskStore** | Conversation-scoped task / step lifecycle (typed dataclasses on Conversation) | `jvagent/memory/task_store.py` |
| **ActionLoader** | Filesystem discovery, dependency install, dynamic import, archetype check | `jvagent/action/loader/action_loader.py` |
| **JvagentActionsImporter** | `sys.meta_path` finder mapping `jvagent.actions.*` → app `agents/...` paths | `jvagent/action/loader/importer.py` |
| **ToolRegistry / ToolExecutionEngine** | Provider-agnostic tool registration + concurrent dispatch | `jvagent/tooling/tool_registry.py`, `jvagent/tooling/tool_executor.py` |
| **BaseModelAction / LanguageModelAction** | LM provider abstraction with HTTP retries + streaming | `jvagent/action/model/base.py`, `jvagent/action/model/language/base.py` |
| **PageIndex** | Vectorless tree-search RAG over assimilated documents | `jvagent/action/pageindex/` |
| **MCP** | MCP client + sandboxed servers (stdio / streamable_http) for tool federation | `jvagent/action/mcp/` |
| **Cache layer** | TTL caches for Agent/Action/router decisions | `jvagent/core/cache.py` |
| **Graph repair** | Distributed-locked structural repair scheduler | `jvagent/core/graph_repair.py`, `jvagent/core/repair_phases/engine.py` |
| **Bundler** | Generates per-app `Dockerfile` from discovered `info.yaml` deps | `jvagent/bundle/bundler.py` |
| **Scaffold** | `jvagent app create` / `agent create` / `skill add` profile resolution | `jvagent/scaffold/operations.py`, `jvagent/scaffold/builtin_profiles/` |

## Pattern Overview

**Overall:** Object-Spatial Programming (OSP) on a persistent graph, with a pluggable plugin/registry system layered on top. Walker-driven traversal supplies the execution model; YAML descriptors supply the wiring.

**Key Characteristics:**
- **Graph-first:** Every long-lived entity (App, Agent, Actions, Action, Memory, User, Conversation, Interaction) is a `jvspatial.core.Node`; relationships are explicit edges with cascade-delete semantics.
- **Walker-driven execution:** `InteractWalker` (a `jvspatial.core.Walker`) is the only execution engine; visits to Action nodes invoke `@on_visit` handlers.
- **Declarative configuration:** `app.yaml` declares the app + agents list; per-agent `agent.yaml` declares actions + context overrides; per-action `info.yaml` declares archetype + dependencies.
- **Three-namespace plugin system:** `jvagent/`, `contrib/`, `custom/` namespaces partition action identifiers (`namespace/action_name`) to prevent collision.
- **Lifecycle hooks:** `on_register`, `on_reload`, `post_register`, `on_startup`, `on_enable`, `on_disable`, `on_deregister`, `healthcheck` give actions full control over their lifecycle.
- **Provider-agnostic tools:** `Tool` dataclass wraps any async callable with a JSON Schema; `ToolRegistry` dispatches concurrently with timeout/error sanitization.
- **Skill bundles:** Claude-style `SKILL.md` markdown bundles ship under `jvagent/skills/` (built-in) and `agents/<ns>/<id>/skills/` (per-agent), discovered/merged at runtime.
- **Walker-revisit pattern (cockpit):** Each `CockpitInteractAction` visit executes one model call; when tool calls remain, state is persisted on `visitor._skill_state` and the action re-prepends itself to the walk path.

## Layers

**CLI layer:**
- Purpose: Parse argv, route to subcommand or HTTP server, set up env / DB / logging
- Location: `jvagent/cli/`
- Contains: `main.py` (dispatcher), `commands.py` (subcommand handlers), `app_commands.py` (`jvagent app create / profile new`), `bootstrap.py`, `server_config.py`
- Depends on: jvspatial Server, dotenv, internal `core.config`
- Used by: `jvagent` console script (entry point), `python -m jvagent`

**Server / HTTP layer:**
- Purpose: FastAPI server (jvspatial.api.Server) with `@endpoint`-decorated handlers
- Location: endpoint modules across `jvagent/core/endpoints.py`, `jvagent/memory/endpoints.py`, `jvagent/action/endpoints.py`, `jvagent/logging/endpoints.py`, plus per-action `endpoints.py`
- Contains: agent CRUD, action CRUD, interact, memory admin, auth, OAuth callbacks (Google/Microsoft), webhooks (whatsapp/postiz/facebook/page-index)
- Depends on: jvspatial.api endpoint registry
- Used by: external HTTP clients, jvchat frontend

**Core layer (graph + bootstrap):**
- Purpose: Define App/Agent/Agents nodes, descriptor loaders, configuration resolution, caching, repair
- Location: `jvagent/core/`
- Contains: 30+ modules covering bootstrap (`app_loader`, `agent_loader`), validation (`*_yaml_validator`), env resolution (`env_resolver`), graph repair (`graph_repair*`, `repair_phases/`), public URL generation, observability hooks
- Depends on: jvspatial.core (Node, Walker, Root), pyyaml
- Used by: CLI, HTTP layer, action subsystem

**Action layer (plugin runtime):**
- Purpose: Pluggable execution units with strict directory contract
- Location: `jvagent/action/<namespace?>/<action_name>/`
- Contains: ~38 actions including interact (`interact/`, `agent_interact/`, `cockpit/`, `router/`, `skill/`, `intro/`, `converse/`, `interview/`, `task_*`, `handoff_interact_action/`), models (`model/language/{anthropic,openai,ollama,openrouter}`, `model/embedding/...`), integrations (`google/`, `microsoft/`, `whatsapp/`, `facebook_action/`, `email_action/`, `postiz_action/`, `mcp/`), retrieval (`pageindex/`, `vectorstore/`, `web_search/`, `web_search_retrieval/`, `retrieval/`, `long_memory*/`), AV (`avatar_action/`, `tts_action/`, `stt_action/`, `video_generation/`), and the `loader/` subpackage that imports them
- Depends on: jvspatial.core, `jvagent.action.base`, `jvagent.tooling`
- Used by: agent.yaml-driven bootstrap; runtime via `Agent.get_action()` / `Action.get_action(...)`

**Memory layer:**
- Purpose: User, conversation, and interaction state persistence
- Location: `jvagent/memory/`
- Contains: `Memory`, `User`, `Conversation`, `Interaction` nodes; `TaskStore`, `EvidenceLog`, `UserLongMemory`, `lock_manager`, `distributed_conversation_lock`, services
- Depends on: jvspatial Node + DeferredSaveMixin
- Used by: every interact path

**Tooling layer:**
- Purpose: Provider-agnostic tool primitives consumed by cockpit / skill loops
- Location: `jvagent/tooling/`
- Contains: `Tool` (dataclass), `ToolRegistry`, `ToolExecutionEngine`, `ToolResult`, `ToolSerializer`, `ToolSchemaValidator`, `ToolObservability`
- Depends on: stdlib only (no jvspatial coupling)
- Used by: `cockpit/registry.py`, `skill/tool_executor.py`, MCP

**Skills layer (built-in catalog):**
- Purpose: Claude-style `SKILL.md` bundles bundled with jvagent
- Location: `jvagent/skills/`
- Contains: 18 bundles (`skill_hub`, `research`, `web_search`, `answer`, `calendar`, `gmail`, `google_drive`, `google_sheets`, `outlook_calendar`, `outlook_mail`, `microsoft_excel`, `microsoft_onedrive`, `fileinterface`, `triage`, `code_review`, `pageindex_docs`, `pageindex_search`, `pdf_generation`)
- Depends on: scaffold resolver + `SKILL.md` parser
- Used by: cockpit / skill_interact_action via `SkillCatalog`

**Scaffold layer:**
- Purpose: Project / agent / skill creation; profile resolution
- Location: `jvagent/scaffold/`
- Contains: `operations.py`, `profile_resolve.py`, `skill_resolve.py`, `yaml_io.py`, `resource_io.py`, `builtin_profiles/{minimal,conversational,agentic,research,whatsapp_voice}.yaml`, `static/env.example.txt`
- Depends on: pyyaml, importlib.resources
- Used by: `jvagent app create`, `jvagent agent create`, `jvagent skill add`

**Bundling layer:**
- Purpose: Per-app Dockerfile generation discovering action dependencies
- Location: `jvagent/bundle/`
- Contains: `bundler.py`, `dockerfile_generator.py`, base `Dockerfile.base`
- Used by: `jvagent bundle [app_root]`

## Data Flow

### Bootstrap (server start)

1. `jvagent/__main__.py` or console script → `jvagent.cli.main:main()` (`jvagent/cli/main.py:118`).
2. `_first_app_root_path()` strips the directory token; `load_app_env()` loads `<app_root>/.env`; `set_app_root()` records the path globally (`jvagent/core/app_context.py`).
3. `_set_db_env_from_config()` resolves DB type/path from `app.yaml` and exports `JVSPATIAL_DB_TYPE` / `JVSPATIAL_DB_PATH`.
4. `run_server()` creates the `jvspatial.api.Server` via `create_server_from_config()` (`jvagent/cli/server_config.py:63`).
5. Endpoint modules are imported for `@endpoint` registration (`_import_core_endpoint_modules`).
6. `pre_startup_bootstrap()` runs `bootstrap_application_graph()`:
   - `AppLoader.bootstrap_application()` ensures Root → App, syncs context (`jvagent/core/app_loader.py`).
   - `AgentLoader` discovers `agents/<ns>/<name>/agent.yaml`, registers `Agents` branchpoint, creates/updates each `Agent`, then `ActionLoader` walks each `actions:` entry and calls `Actions.register_action()`.
   - `Action.on_register()` / `on_reload()` / `post_register()` lifecycle hooks fire.
7. `run_app_startup()` invokes every action's `on_startup()` so runtime components (channel adapters, MCP clients, model HTTP pools) initialize.
8. `ensure_admin_user()` creates the admin from `JVAGENT_ADMIN_PASSWORD` if missing.
9. `server.run()` hands off to uvicorn.

### Interaction request (default path)

1. Client → `POST /interact` (or `/interact/stream`) handled by `jvagent/action/interact/endpoints.py:interact()`.
2. Rate limiter checks (`jvagent/action/interact/rate_limiter.py`); auth + agent_id validated; `Agent.get(agent_id)` (cached).
3. `InteractWalker(agent_id, utterance, channel, session_id, user_id, ...)` is constructed; `initialize_interaction()` resolves user → conversation → new `Interaction` (`jvagent/action/interact/interact_walker.py:50`).
4. `walker.spawn(agent)` jumps directly to the Agent node (skipping Root traversal).
5. Walker traverses `Agent → Actions → InteractAction[]` in `weight` order. For each top-level `InteractAction`:
   - `AccessControlAction` (if any) gates the visit.
   - `@on_visit(InteractAction)` invokes `InteractAction.execute(walker)`.
   - Top-level actions explicitly route to children via `visitor.visit(...)` or `visitor.prepend([...])`.
6. **Cockpit path** (`CockpitInteractAction.execute`):
   - Phase 1 — `CockpitRouter.route()` classifies posture (RESPOND/SUPPRESS/DEFER) + selects skills via a fast LM call; canned lead-in optionally streamed.
   - Phase 2 — On first visit, `CockpitEngine` is constructed; `assemble_cockpit_tools()` (`jvagent/action/cockpit/registry.py`) merges harness tools (memory, response, task, conversation, skill, search, artifact) + action tools (`Action.get_tools()`) + skill bundle tools.
   - `engine.step()` executes ONE model call. If tool calls returned: `ToolExecutionEngine.execute(...)` runs them concurrently; engine state persisted on `visitor._skill_state`; action re-prepends itself via `visitor.prepend([self])` and walker re-visits.
   - When step returns a final text response: `deliver_final_response()` / `deliver_conversational()` publish via `ResponseBus`.
7. `ResponseBus` (`jvagent/action/response/response_bus.py`) streams adhoc chunks during the loop and the final `ResponseMessage` to channel adapter subscribers.
8. After walker completes, `_finalize_usage()` computes per-interaction usage from `observability_metrics`, updates user totals, and `flush_deferred_entities()` persists Conversation/Interaction snapshots.
9. `build_interact_response()` assembles the HTTP response (or SSE stream).

### Background actions

InteractActions with `run_in_background = True` are deferred. After the user-facing response is sent, they execute via `jvspatial.create_task` so analytics, model updates, and long-memory writes do not block the client.

### State Management

- **Graph state:** persisted as jvspatial nodes/edges in the configured DB backend.
- **Per-interaction transient state:** carried on the walker (`visitor._skill_state`, `visitor.context`) and discarded when the walker terminates.
- **Streaming state:** held in `ResponseBus._session_queues` / `_message_buffers` keyed by `interaction_id`; cleared after delivery.
- **Caches:** `cache_manager` (Agent/Actions/Action by id, TTL configurable in `app.yaml.config.performance`); `interact_router_cache` for routing decisions.

## Key Abstractions

**Node hierarchy:**
- Purpose: Persistent, edge-connected entities with cascade-delete semantics
- Examples: `jvagent/core/app.py`, `jvagent/core/agent.py`, `jvagent/memory/manager.py`, `jvagent/action/base.py`
- Pattern: Subclass `jvspatial.core.Node`; declare typed fields with `attribute(...)`; declare `@compound_index` for query patterns

**Walker:**
- Purpose: Stateful traversal engine with `@on_visit` dispatch
- Examples: `jvagent/action/interact/interact_walker.py:InteractWalker`
- Pattern: Subclass `jvspatial.core.Walker`; declare walker state as Pydantic fields; spawn on a starting node; visit handlers receive both walker and visited node

**Action archetype:**
- Purpose: Plugin contract — every action declares an archetype class in `info.yaml` matching the Python class
- Examples: every `<action>/info.yaml` lists `package.archetype: <ClassName>`
- Pattern: `class FooAction(Action)` (or `InteractAction`, `LanguageModelAction`, etc.); typed `attribute(...)` defaults overridden by `agent.yaml` `context:` block

**Tool:**
- Purpose: Provider-agnostic callable wrapped with metadata + JSON schema
- Examples: `jvagent/tooling/tool.py:Tool`, harness tools in `jvagent/action/cockpit/{memory,task,response,skill,search,artifact,conversation}_tools.py`
- Pattern: `Tool(name, description, parameters_schema, execute)`; `await tool.call(**args) -> ToolResult`; registered in a `ToolRegistry`

**Skill bundle:**
- Purpose: Claude-style markdown SOP + tool scripts loadable at runtime
- Examples: `jvagent/skills/research/SKILL.md`, `jvagent/skills/skill_hub/_skills_cli.py`
- Pattern: `SKILL.md` frontmatter (`name`, `description`, `allowed-tools`, `tags`) + workflow body + sibling `*.py` tool scripts; resolved by `jvagent/scaffold/skill_resolve.py`

**ResponseMessage / Channel adapter:**
- Purpose: Outbound message envelope with channel-specific filtering
- Examples: `jvagent/action/response/message.py`, `jvagent/action/response/channel_adapter.py`, `jvagent/action/whatsapp/`, `jvagent/action/facebook_action/`
- Pattern: Adapter actions register with `ResponseBus.subscribe()`; messages flow through `ChannelFilter` chain before delivery

**TaskStore / Task / Step:**
- Purpose: Conversation-scoped progress tracking exposed to the model
- Examples: `jvagent/memory/task_store.py`
- Pattern: `await store.create(...)`, `task.start()`, `task.add_step(...)`, `step.complete(result=...)`

## Entry Points

**Console script `jvagent`:**
- Location: declared in `pyproject.toml` `[project.scripts]` → `jvagent.cli:main`
- Triggers: terminal invocation
- Responsibilities: full CLI dispatcher (run / status / agent / skill / action / bootstrap / bundle / app / validate / stress-seed)

**Module `python -m jvagent`:**
- Location: `jvagent/__main__.py`
- Triggers: `python -m jvagent ...`
- Responsibilities: thin shim into `jvagent.cli.main`

**HTTP server (default subcommand):**
- Location: `jvagent/cli/main.py:run_server` → `jvagent/cli/server_config.py:create_server_from_config`
- Triggers: `jvagent` with no subcommand, `jvagent run`
- Responsibilities: build `jvspatial.api.Server`, register all `@endpoint` modules, run uvicorn

**Interact endpoint:**
- Location: `jvagent/action/interact/endpoints.py`
- Triggers: `POST /interact`, `POST /interact/stream`
- Responsibilities: rate-limit → InteractWalker spawn → response delivery

**OAuth callback endpoints:**
- Location: `jvagent/action/google/endpoints.py`, `jvagent/action/microsoft/endpoints.py`
- Triggers: provider redirect URI
- Responsibilities: complete OAuth flow, persist tokens on the agent

**Webhook endpoints:**
- Location: `jvagent/action/whatsapp/endpoints.py`, `jvagent/action/facebook_action/endpoints.py`, `jvagent/action/postiz_action/endpoints.py`, `jvagent/action/pageindex/endpoints.py`
- Triggers: external service callbacks
- Responsibilities: verify signature, transform payload into an interaction or domain event

**Repair / admin endpoints:**
- Location: `jvagent/core/endpoints.py`, `jvagent/memory/endpoints.py`, `jvagent/action/endpoints.py`, `jvagent/action/access_control/endpoints.py`, `jvagent/logging/endpoints.py`
- Triggers: admin clients
- Responsibilities: agent CRUD, action CRUD, memory admin, graph repair, log queries

## Architectural Constraints

- **Threading:** Single-process, asyncio-based; uvicorn workers may be > 1 (configurable). Serverless mode (`--serverless`) forces `workers=1` and disables background tasks (`os.environ["SERVERLESS_MODE"]=true`). Long-running scheduled jobs (graph repair) are skipped under serverless and must be triggered externally.
- **Global state:** Several module-level singletons exist — `jvagent/core/cache.py:cache_manager`, `jvagent/core/app_context.py` (app-root global), action `loader/importer.py:_actions_importer_base_path`, `App._cached_app` ClassVar. Tests that switch app roots must reset these.
- **Circular imports:** `Agent` ↔ `Actions` ↔ `Action` ↔ `InteractAction` ↔ `InteractWalker` are mediated by `TYPE_CHECKING` guards and string-typed imports inside methods. New code must follow the same pattern (defer concrete imports to function bodies when crossing layers).
- **Plugin namespace:** Every Action lives at `<root>/agents/<agent_ns>/<agent_name>/actions/<action_ns>/<action_name>/` (app-local) or `jvagent/action/<action_dir>/` (built-in). The `JvagentActionsImporter` finder enforces this layout.
- **Singleton actions:** Actions with `is_singleton = True` (e.g., `IntroInteractAction`, `PersonaAction`) reject duplicate registration per agent; enforced in `Actions.register_action()`.
- **Top-level routing:** Top-level `InteractAction` nodes connected directly to `Actions` MUST explicitly traverse children via `visitor.visit(...)` — the walker does not auto-recurse from the top tier (only top-tier weight ordering is honored).
- **Update modes:** `--update` + `--source` is destructive (deletes and recreates Action nodes — child nodes are lost); `--update --merge` (default) is non-destructive and preserves DB state. The `App.update_mode` attribute is `protected=True` and may only be mutated through `set_app_update_mode()`.
- **Cockpit walker-revisit:** Each `CockpitInteractAction.execute()` performs ONE model call; iteration is achieved by re-prepending `self` to the walk path and persisting state on `visitor._skill_state`. Engines must be JSON-serializable on the visitor.

## Anti-Patterns

### Importing concrete InteractWalker / InteractAction at module top of cross-layer files

**What happens:** Tests and runtime crash on circular import (Action → InteractAction → InteractWalker → InteractAction).
**Why it's wrong:** The interact subsystem is intentionally a leaf consumer of the Action base; pulling it upstream creates a cycle.
**Do this instead:** Use `TYPE_CHECKING` for type hints (`jvagent/action/interact/interact_walker.py:24`) and lazy-import inside methods (e.g., `jvagent/action/__init__.py:13` `__getattr__`).

### Calling `Action.save()` mid-walker without flushing

**What happens:** Conversation/Interaction snapshots inconsistent with walker outcome; deferred saves lost.
**Why it's wrong:** `Conversation` and `Interaction` use `DeferredSaveMixin`; explicit saves must coordinate with `flush_deferred_entities()` (called in `_finalize_usage`).
**Do this instead:** Mutate state through the provided helpers (`walker.record_action(...)`, `interaction.compute_usage()`); let the endpoint's `_finalize_usage()` handle persistence (`jvagent/action/interact/endpoints.py:39`).

### Manually instantiating `Action` subclasses outside the loader

**What happens:** Action lacks `metadata`, never has `on_register()` called, fails singleton check.
**Why it's wrong:** `ActionLoader.factory.build_action_metadata_payload()` builds the metadata payload from `info.yaml`; bypassing it produces inconsistent state.
**Do this instead:** Add the action to `agent.yaml` `actions:` and run `jvagent --update` (or `jvagent bootstrap --update`); for ad-hoc programmatic use, instantiate via `ActionLoader.load_action(...)`.

### Reading raw `os.environ` for `app.yaml`-derived config

**What happens:** Config drift between CLI subcommands and HTTP server; `_set_db_env_from_config` overrides ignored.
**Why it's wrong:** `app.yaml.config.*` resolution is centralized in `jvagent/core/config.py:get_config_value` with priority env > yaml > default.
**Do this instead:** Always go through `load_app_config(app_root)` + `get_config_value(...)`.

### Putting heavy logic in module bodies of action packages

**What happens:** Every `from jvagent.action.X` import pays the cost; bootstrap slows; circular import risk grows.
**Why it's wrong:** The `ActionLoader` imports modules during agent bootstrap; module-level side effects (HTTP clients, model loads) break startup ordering.
**Do this instead:** Defer to `on_register` / `on_startup` lifecycle hooks (`jvagent/action/base.py:85`).

### Storing skill state on the action node itself

**What happens:** Concurrent interactions corrupt each other; persistence keeps stale state across requests.
**Why it's wrong:** Action nodes are shared across all interactions for an agent.
**Do this instead:** Stash per-interaction state on the walker (`visitor._skill_state[<key>]`) — it lives only for that walk.

## Error Handling

**Strategy:** Layered. Endpoint layer raises `jvspatial.api.exceptions.*` (`ValidationError`, `ResourceNotFoundError`, `RateLimitError`, `JVSpatialAPIException`) which the server translates into HTTP responses. Domain layer catches and logs with context. Tool execution layer sanitizes errors to avoid leaking provider internals (`ToolExecutionEngine.sanitize_errors=True` default).

**Patterns:**
- LM HTTP retries: configurable `max_retries`, `retry_initial_delay`, `retry_max_delay`, `retry_backoff_multiplier`, `retry_jitter`, `retry_on_status_codes` on every `BaseModelAction` (`jvagent/action/model/base.py`).
- Validation: pydantic field validation at Node load + `validate_app_yaml_descriptor` / `validate_agent_yaml` warnings during `jvagent validate` (`jvagent/cli/commands.py:150`).
- Bootstrap: `BootstrapLogger` + `StartupLogCounter` aggregate WARNING/ERROR/CRITICAL counts and surface them post-startup (`jvagent/cli/commands.py:324`).
- Walker: `try/except` around each `@on_visit` handler in jvspatial; `InteractWalker.initialize_interaction()` returns a typed `InteractionInitResult` with machine-readable codes.
- Graph repair: `repair_agent_graph(max_seconds=...)` runs under a distributed lock with grace-period safeguards.

## Cross-Cutting Concerns

**Logging:**
- Standard `logging` configured by `jvspatial.logging.configure_standard_logging()` in `jvagent/cli/main.py:31`.
- Custom `INTERACTION` level (22) registered in `jvagent/logging/__init__.py`.
- `DBLogHandler` writes structured logs to the configured log DB (json/sqlite/mongo/dynamo); endpoints expose querying (`jvagent/logging/endpoints.py`).
- Observability hooks: `jvagent/core/observability.py` lets actions emit structured events; tool calls emit `ToolExecutionEnvelope` / `SkillActivationEnvelope` (`jvagent/tooling/tool_observability.py`).

**Validation:**
- `jvagent/core/app_yaml_validator.py` and `jvagent/core/agent_yaml_validator.py` produce typed `ValidationIssue` warnings consumed by `jvagent validate` (CI-friendly exit code).
- Pydantic models on every Node enforce field types at construction.
- Tool parameter schemas validated by `jvagent/tooling/tool_schema_validator.py` against OpenAI strict-mode rules at `Tool.__post_init__`.

**Authentication:**
- jvspatial.api.auth provides JWT auth; admin user bootstrapped from `JVAGENT_ADMIN_PASSWORD`.
- `@endpoint(auth=True, roles=["admin"])` decorator gates admin endpoints; per-action `AccessControlAction` (jvagent/action/access_control/) gates execution within the walker.
- OAuth providers (Google, Microsoft) handled in `jvagent/action/google/google_token.py`, `jvagent/action/microsoft/microsoft_token.py`.

**Configuration:**
- Priority everywhere: env var > `app.yaml`/`agent.yaml` > hardcoded default. Implemented uniformly in `jvagent/core/config.py:get_config_value`.
- `${ENV_VAR}` placeholders in YAML resolved by `jvagent/core/env_resolver.py:resolve_env_placeholders`.

**Performance:**
- TTL caches: agent/actions/action/router (`jvagent/core/cache.py`).
- Deferred saves: `DeferredSaveMixin` on `Conversation` and `Interaction` (`flush_deferred_entities` at end of walker).
- Profiling: `jvagent/core/profiling.py` with `JVSPATIAL_*_PROFILING` env keys; controlled per app.

**Background work:**
- `jvspatial.create_task` schedules background coroutines (used by `_finalize_usage`, callbacks, run-in-background InteractActions).
- `apscheduler` cron jobs for graph repair when `JVAGENT_REPAIR_SCHEDULE_CRON` set (skipped in serverless mode).

---

*Architecture analysis: 2026-05-06*
