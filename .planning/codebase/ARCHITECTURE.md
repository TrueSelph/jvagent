# Architecture

**Analysis Date:** 2026-05-06

## System Overview

jvagent is a modular AI agent platform built on `jvspatial`'s graph-based node-and-edge primitives. The architecture uses a declarative YAML configuration system to define applications, agents, and actions. The system follows a hierarchical graph structure where all entities (App, Agent, Actions, Memory) are Nodes connected via edges.

```text
┌──────────────────────────────────────────────────────────────────┐
│                          Entry Point (CLI)                        │
│                     `jvagent.cli.main.py`                         │
├──────────────────────────────────────────────────────────────────┤
│                      Bootstrap / Server Init                      │
│           AppLoader → AgentLoader → ActionRegistration            │
├──────────────────────────────────────────────────────────────────┤
│                    Root Node Graph Hierarchy                      │
│  Root → App → [Agents → Agent → [Actions → Action, Memory]]     │
│                                     ↓                             │
│                    [User → Conversation → Interaction]            │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Agent Interaction Pipeline                     │
│            HTTP POST /interact (InteractAction endpoints)         │
├──────────────────────────────────────────────────────────────────┤
│  1. InteractWalker Spawn (starts on Agent node)                   │
│  2. User/Conversation Resolution (Memory lookup)                  │
│  3. Interaction Node Creation (persisted in DB)                   │
│  4. Access Control Check (AccessControlAction)                    │
│  5. Top-Level InteractAction Execution (weight-ordered):          │
│     - InteractRouter (if enabled) → routes posture/intent        │
│     - PersonaAction/Converse/SkillAction → executes main logic   │
│     - Background Actions (post-interaction async tasks)           │
│  6. Response Building & Streaming                                 │
│  7. Response Publishing (saves Interaction, publishes to bus)     │
└──────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **App** | Root application node; manages app-level settings, timezones, file storage, logging | `jvagent/core/app.py` |
| **Agents** | Structural branchpoint for agent collection; maintains aggregate counters | `jvagent/core/agents.py` |
| **Agent** | Individual agent node; CRUD interface for agent operations; relationship hub | `jvagent/core/agent.py` |
| **Actions** | Central manager for action registration, discovery, statistics | `jvagent/action/actions.py` |
| **Action** | Base class for all pluggable actions; defines lifecycle hooks and configuration pattern | `jvagent/action/base.py` |
| **InteractAction** | Specialized Action for interact subsystem participation (routing, execution) | `jvagent/action/interact/base.py` |
| **InteractWalker** | Walker that traverses InteractActions; entry point for interactions | `jvagent/action/interact/interact_walker.py` |
| **Memory** | Root node for user/conversation/interaction management; handles user creation and retrieval | `jvagent/memory/manager.py` |
| **User** | User node; represents end-users interacting with agent | `jvagent/memory/user.py` |
| **Conversation** | Conversation session node; groups interactions under a session | `jvagent/memory/conversation.py` |
| **Interaction** | Single user-agent exchange; stores utterance, response, action trace, metrics | `jvagent/memory/interaction.py` |
| **AppLoader** | Bootstraps App from app.yaml; handles YAML validation and graph initialization | `jvagent/core/app_loader.py` |
| **AgentLoader** | Bootstraps Agent(s) from agent.yaml; handles action registration and config resolution | `jvagent/core/agent_loader.py` |
| **AgentInteractAction** | Unified skill-routing action; combines router + agentic loop in single walker visit | `jvagent/action/agent_interact/agent_interact_action.py` |
| **SkillCatalog** | Registry and discovery for available skills; handles dynamic skill loading | `jvagent/action/skill/skill_catalog.py` |

## Pattern Overview

**Overall:** Hierarchical graph-based object-spatial arrangement (jvspatial) with pluggable, declaratively-configured action pipeline

**Key Characteristics:**
- **Node-centric**: All domain entities (App, Agent, Action, User, Conversation, Interaction) are jvspatial Nodes
- **Edge relationships**: Parent-child and sibling relationships established via jvspatial edges (enables cascade delete, traversal)
- **Declarative YAML**: Actions and agents defined in machine-readable YAML (app.yaml, agent.yaml); property overrides via context blocks
- **Pluggable architecture**: Actions are self-contained packages with metadata, lifecycle hooks, endpoints, and tool definitions
- **Walker-based traversal**: InteractWalker and other custom walkers traverse the graph to execute modular logic
- **Async/await**: Fully async architecture built on asyncio and FastAPI
- **Multi-provider support**: Language models, embeddings, vector stores, and external services abstracted behind common interfaces

## Layers

**Configuration Layer:**
- Purpose: Parse and validate declarative configuration (YAML files)
- Location: `jvagent/core/app_loader.py`, `jvagent/core/agent_loader.py`, `jvagent/core/app_yaml_validator.py`, `jvagent/core/agent_yaml_validator.py`
- Contains: YAML parsing, validation, environment variable resolution, dependency installation
- Depends on: jvspatial, pyyaml, Python standard library
- Used by: Bootstrap process, CLI, startup handlers

**Graph/Persistence Layer:**
- Purpose: Manage Node/Edge relationships, database I/O, caching
- Location: jvspatial library (external dependency); local extensions in `jvagent/core/cache.py`, `jvagent/core/graph_repair.py`
- Contains: Node definition, attribute management, indexing, query execution, cascade deletes
- Depends on: MongoDB backend, jvspatial
- Used by: All domain entities

**Domain Layer:**
- Purpose: Represent core business entities (App, Agent, Action, Memory, User, Conversation, Interaction)
- Location: `jvagent/core/`, `jvagent/action/`, `jvagent/memory/`
- Contains: Node class definitions, CRUD operations, relationship management, lifecycle hooks
- Depends on: jvspatial, configuration layer, graph layer
- Used by: API endpoints, interaction handlers, loaders

**Action/Plugin Layer:**
- Purpose: Provide pluggable, composable units of functionality
- Location: `jvagent/action/` (base classes and built-in actions)
- Contains: Action base class, InteractAction base class, lifecycle hooks, action-to-action communication
- Depends on: Domain layer, external SDKs (e.g., OpenAI, Google APIs, Stripe)
- Used by: Bootstrap (registration), InteractWalker (execution), skill system

**Interaction/Execution Layer:**
- Purpose: Execute interactions through modular pipeline of InteractActions
- Location: `jvagent/action/interact/`, `jvagent/action/agent_interact/`, `jvagent/action/skill/`
- Contains: InteractWalker, routing logic, skill catalog, tool execution, agentic think-act-observe loop
- Depends on: Action layer, domain layer, LM providers
- Used by: HTTP API, response bus

**API/HTTP Layer:**
- Purpose: Expose functionality via RESTful endpoints
- Location: `jvagent/core/endpoints.py`, `jvagent/action/endpoints.py`, `jvagent/action/interact/endpoints.py`, `jvagent/memory/endpoints.py`
- Contains: FastAPI endpoint decorators, request/response handling, auth/RBAC
- Depends on: FastAPI, jvspatial API framework, domain/action layers
- Used by: External clients, CLI commands

## Data Flow

### Primary Request Path (User Interaction)

1. **HTTP POST /agents/{agent_id}/interact** (`jvagent/action/interact/endpoints.py`)
   - Parses request body (utterance, user_id, channel, session_id, data)
   - Validates user/agent existence
   - Initiates InteractWalker for the agent

2. **InteractWalker Initialization** (`jvagent/action/interact/interact_walker.py`)
   - Resolves or creates User in Memory
   - Resolves or creates Conversation (session-scoped)
   - Initializes Interaction node in graph
   - Populates walker state (utterance, user, conversation, etc.)

3. **Walker Spawn on Agent** (`jvagent/action/interact/interact_walker.py::spawn()`)
   - Traverses Agent → Actions → InteractActions
   - Fetches top-level InteractActions from Actions node
   - Sorts by weight (lower = earlier execution)

4. **Access Control Check** (`jvagent/action/access_control/`)
   - AccessControlAction verifies user/agent permissions
   - Returns early if denied; logs access denied event

5. **Core InteractAction Execution** (multiple actions in sequence)
   - **InteractRouter** (if enabled) (`jvagent/action/agent_interact/router/`)
     - Calls LM to classify user intent/posture (task, clarification, banter, etc.)
     - Records routing result on interaction
     - Sets anchors/routing flags for downstream actions
   
   - **AgentInteractAction** (unified router+skill) (`jvagent/action/agent_interact/agent_interact_action.py`)
     - If conversational (banter/greeting): calls PersonaAction.respond_slim for fast path
     - If task: enters agentic skill loop (AgentInteractSkillAction)
   
   - **SkillAction Loop** (`jvagent/action/skill/skill_action.py`)
     - Initializes skill loop context (goals, current step, checkpoint)
     - Repeatedly:
       1. Builds system prompt with persona + skill catalog
       2. Calls LM with available tools (skills as tool definitions)
       3. Parses tool calls from LM response
       4. Executes tools via ToolExecutor (`jvagent/action/skill/tool_executor.py`)
       5. Records tool results, stuck detection, loop checkpoint
     - Exits when goal achieved or max iterations reached
   
   - **PersonaAction** (if needed) (`jvagent/action/persona/persona_action.py`)
     - Formats response according to persona description
     - Applies voice/tone directives from router
   
   - **Background Actions** (deferred execution)
     - Collected during walk; executed after response published

6. **Response Publishing**
   - Interaction node saved to database with all action results, metrics, usage
   - Response published to ResponseBus (if present)
   - Streamed or batch-returned to client

### Secondary Flows

**Agent Bootstrap:**
1. AppLoader reads app.yaml → creates App node, sets up storage, logging
2. AgentLoader reads agent.yaml → creates Agent node
3. Action discovery: scan declared actions, resolve dependencies
4. Action registration: create Action nodes, call on_register() hooks
5. Skill discovery: populate SkillCatalog from filesystem and action metadata

**Background Task Execution:**
1. Interaction marked as closed; response sent to client
2. Background actions executed asynchronously (fire-and-forget)
3. Examples: model fine-tuning, analytics, database cleanup

**Graph Repair (Maintenance):**
1. Triggered via `/admin/graph-repair` endpoint
2. Runs memory repair (user/conversation/interaction tree consistency)
3. Runs structural repair (orphaned nodes, broken edges)
4. Runs interaction limit pruning (rolling window per user)

**State Management:**
- **Transient state**: Walker state (utterance, user, conversation, action results) held in InteractWalker instance during interaction
- **Persistent state**: Interaction, User, Conversation, Action, Agent nodes persisted to MongoDB
- **Cache state**: AgentCache (agent lookups), ActionCache (action resolution), SkillCatalog (in-memory skill index)
- **Runtime singletons**: App instance (per-process), ResponseBus (per-agent), LM action instances

## Key Abstractions

**Action:**
- Purpose: Pluggable unit of functionality attached to an agent
- Examples: `jvagent/action/agent_interact/agent_interact_action.py`, `jvagent/action/persona/persona_action.py`, `jvagent/action/model/language/openai/` (OpenAI language model)
- Pattern: Extends `Action` base class; defines attributes via `attribute()` descriptors; implements lifecycle hooks (`on_register`, `on_enable`, `on_disable`, etc.)

**InteractAction:**
- Purpose: Action that participates in the interact subsystem (walker traversal)
- Examples: `jvagent/action/agent_interact/agent_interact_action.py`, `jvagent/action/converse/converse_action.py`
- Pattern: Extends `InteractAction`; implements `execute(visitor)` method; weight-ordered at top tier

**Skill:**
- Purpose: Reusable capability invoked as tool in agentic loop
- Examples: `jvagent/skills/gmail/`, `jvagent/skills/google_drive/`, `jvagent/skills/web_search/`
- Pattern: Defined in SKILL.md file (YAML); discovered at startup; registered in SkillCatalog; executed via ToolExecutor

**Visitor/Walker:**
- Purpose: Traversal mechanism for graph-based logic
- Examples: `jvagent/action/interact/interact_walker.py` (InteractWalker)
- Pattern: Extends `jvspatial.core.Walker`; implements `on_visit()` hooks; carries execution state through traversal

**Memory Hierarchy:**
- **Memory**: Root node for agent's conversational memory
- **User**: Represents an end-user; connected to Memory via edge
- **Conversation**: Session for grouped interactions; connected to User via edge
- **Interaction**: Single user-agent exchange; chained bidirectionally; connected to Conversation via edge

**Tool/Skill Execution:**
- Purpose: Execute tool calls issued by LM within skill loop
- Examples: `jvagent/action/skill/tool_executor.py`
- Pattern: Registered tools have schema (via jvspatial.tooling); executor parses tool calls, invokes registered handlers, captures results

## Entry Points

**CLI Entry Point:**
- Location: `jvagent.cli.main.main()`
- Triggers: `jvagent` command, `python -m jvagent`
- Responsibilities: Parse CLI arguments, dispatch to subcommands (bootstrap, server, agent admin, skill admin), handle app initialization

**HTTP Entry Point (Agent Interaction):**
- Location: `jvagent/action/interact/endpoints.py::interact()` (POST `/agents/{agent_id}/interact`)
- Triggers: HTTP POST request
- Responsibilities: Parse request, spawn InteractWalker, execute interaction pipeline, return response

**HTTP Entry Point (Admin):**
- Location: `jvagent/core/endpoints.py` (agent CRUD), `jvagent/memory/endpoints.py` (memory admin)
- Triggers: HTTP GET/PUT/DELETE requests
- Responsibilities: Manage agents, repair graph, list interactions

**Bootstrap Entry Point:**
- Location: `jvagent/cli/bootstrap.py`
- Triggers: `jvagent [app_path] bootstrap` or implicit on first run
- Responsibilities: Initialize app from app.yaml, register agents/actions from agent.yaml, create initial graph structure

## Architectural Constraints

- **Threading:** Single-threaded event loop (asyncio); all I/O is async; blocking calls prohibited
- **Global state:** App singleton per process (cached via AppLoader); ResponseBus singleton per agent; SkillCatalog singleton per agent
- **Circular imports:** AgentLoader delays import of AgentDescriptor; InteractAction delays import of InteractWalker via try/except; core/__init__.py uses `__getattr__` for lazy imports
- **Cascade deletes:** Deleting User → cascades to all Conversations → cascades to all Interactions; enables clean memory pruning
- **Node uniqueness:** Actions are uniquely identified by (agent_id, namespace, label); singleton actions enforced at registration time
- **Configuration resolution:** Attributes defined in Action class; overridden in agent.yaml context block; validated by Pydantic at runtime
- **Interaction closure:** Once interaction.closed=True, no further modifications allowed; background actions executed post-closure
- **Memory pruning:** Interaction limit per user (rolling window); oldest interactions deleted when limit exceeded; configured per agent

## Anti-Patterns

### Over-Persistence of Transient State

**What happens:** Storing walker state (current action, temporary flags, loop checkpoints) as Action attributes on disk, then querying them later
**Why it's wrong:** Walker state is ephemeral and interaction-scoped; persisting it couples the walker to storage, makes concurrent interactions conflict, pollutes the action's permanent state
**Do this instead:** Keep all transient state in the walker instance or a scoped context object (e.g., `AgentInteractSkillRunContext` in `jvagent/action/agent_interact/skill/context.py`); save only the final result to Interaction

### Blocking I/O in Action Methods

**What happens:** Calling `requests.get()` or `time.sleep()` directly in action methods
**Why it's wrong:** Blocks the event loop; other interactions starve; entire server becomes unresponsive
**Do this instead:** Use `httpx.AsyncClient`, `asyncio.sleep()`, or other async libraries; all action methods must be `async def`

### Hardcoding Namespace/Label Assumptions

**What happens:** Assuming action labels like `"my_action"` will always exist, or hardcoding namespace paths
**Why it's wrong:** Actions can be disabled, renamed, or removed; hardcoded assumptions break under refactoring
**Do this instead:** Use `get_action(ActionClass)` or `get_action("ClassName")` to resolve actions dynamically; check for None return; provide fallback behavior

### Bypassing the Walker for Interaction Logic

**What happens:** Directly instantiating and calling interact actions from non-walker contexts
**Why it's wrong:** Loses walker state management, access control checks, response bus subscription, background action collection
**Do this instead:** Always use InteractWalker for user-facing interactions; use walker visitor methods to route/execute actions within the walker context

## Error Handling

**Strategy:** Layered error handling with early returns and logging

**Patterns:**
- Bootstrap errors: Logged and printed to console; app may start in degraded mode
- Graph query errors: Returned as None (lookup failures); caller checks and handles
- Action execution errors: Caught by InteractWalker; recorded in interaction.actions; response builder handles gracefully
- Validation errors: Raised as `ValidationError` at parse time (YAML, config); prevents bad state
- Database errors: Propagated as StorageError; logged; interaction marked as failed

## Cross-Cutting Concerns

**Logging:** Configured via `jvspatial.logging.configure_standard_logging()`; routed to DB (DBLogHandler) and console; level controlled by `JVSPATIAL_LOG_LEVEL` env var (`jvagent/logging/`)

**Validation:** Pydantic models for request/response bodies; YAML schema validation for app.yaml/agent.yaml; attribute typing enforced at Node creation

**Authentication:** Auth layer provided by jvspatial API framework; roles-based access control (RBAC) in endpoints; AccessControlAction provides custom per-action auth logic

**Observability:** Hooks registered via `register_observability_hook()`; events emitted for model calls, tool executions, action state changes; aggregated in Interaction node as observability_metrics

**Metrics & Usage:** Tracked per interaction (token counts, LM calls, tool calls); aggregated in Interaction.usage; queried via memory endpoints

---

*Architecture analysis: 2026-05-06*
