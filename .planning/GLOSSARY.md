# Glossary

Single-source definitions for terms used throughout jvagent. Whenever a defined term appears for the first time in another doc, it links here.

---

### Action
Pluggable component that extends an agent. Subclass of jvspatial `Node`. Source: [`jvagent/action/base.py:48`](../jvagent/action/base.py). Has lifecycle hooks (`on_register`, `on_enable`, etc.), attribute config, optional endpoints, optional tools for the cockpit. Lives in a directory under `jvagent/action/{namespace}/{action_name}/`. See [`action-authoring.md`](action-authoring.md).

### Actions (node)
Branchpoint node connecting an `Agent` to its `Action` children. One per agent. Accessed via `await agent.get_actions_manager()` ([`agent.py:89`](../jvagent/core/agent.py)).

### Agent
Logical execution unit. One graph node per agent; many per app. Source: [`jvagent/core/agent.py:18`](../jvagent/core/agent.py). Owns an `Actions` subgraph and a `Memory` subgraph.

### App
Singleton root node for the application. Source: [`jvagent/core/app.py:19`](../jvagent/core/app.py). Manages app-level settings (timezone, file storage, update mode) and is accessed as a singleton via `App.get()`.

### `app.yaml`
Declarative configuration file at the app root. Defines app-level settings: app_id, name, version, file storage, database backend, logging. Read by `app_loader`. See [`configuration-keys.md`](configuration-keys.md).

### `agent.yaml`
Declarative configuration file per agent (under `agents/{namespace}/{agent_name}/agent.yaml`). Defines the agent's actions and per-action `context:` overrides. Read by `agent_loader`.

### Anchor
Short statement an `InteractAction` publishes to the `InteractRouter` to advertise when it should be selected. Defined as `anchors: List[str]` attribute on `InteractAction` ([`interact/base.py:99`](../jvagent/action/interact/base.py)). Can be dynamic via `get_anchors()`.

### Background action
An `InteractAction` with `run_in_background=True` ([`interact/base.py:88`](../jvagent/action/interact/base.py)). Queued by the walker and executed as a fire-and-forget asyncio task **after** the user-facing response is sent. Failures are isolated.

### `BaseModelAction`
Base class for any model-using action (LLM, embedding, etc.). Source: `jvagent/action/model/base.py:26`. Provides retry config (`max_retries`, `retry_backoff_multiplier`, etc.).

### `info.yaml`
Per-action package metadata file. Defines package name, archetype, version, group, dependencies (jvagent, other actions, pip packages), and default config. Read at action register/load time. Stored on the action as `metadata`.

### Channel adapter
Component that translates response-bus messages to a specific transport (WhatsApp, Messenger, email, SSE, stdout). Registered with the agent's `ResponseBus`. Lives in `jvagent/action/response/channel_adapter.py` and per-channel actions.

### Cockpit
The model-cockpit pattern. Implemented by [`CockpitInteractAction`](../jvagent/action/cockpit/cockpit_interact_action.py:79). One model call per walker visit; if tool calls return, the action re-adds itself to the walk path. The cockpit grants the LLM full agency over harness services and action tools. See [`../docs/COCKPIT.md`](../docs/COCKPIT.md), [`adr/0002`](adr/0002-walker-revisit-cockpit.md).

### `CockpitEngine`
The think-act-observe engine inside the cockpit. Source: `jvagent/action/cockpit/engine.py`. Owns the iteration step.

### `CockpitRouter`
The lightweight pre-cockpit LLM classifier. Returns posture (`RESPOND` | `SUPPRESS` | `DEFER`) and a list of relevant skills. Source: `jvagent/action/cockpit/routing/router.py`.

### `CockpitState`
Per-run state persisted on `visitor._skill_state` between walker visits. Fields: messages, iteration, activated_skills, started_at, tools_serialized, recent_tool_names.

### Conversation
Session-keyed node holding a chained sequence of `Interaction` nodes. Source: `jvagent/memory/conversation.py:39`. Created lazily by `Memory.get_user()`. May override `Agent.interaction_limit`.

### `DeferredSaveMixin`
jvspatial-provided mixin used by `Conversation` and `Interaction` to batch writes for efficiency. Flushed after the walker completes.

### Endpoint
HTTP route defined by the `@endpoint` decorator from jvspatial. Actions ship endpoints in `endpoints.py`. Registered at server bootstrap; auto-cleaned on action deregister ([`base.py:392`](../jvagent/action/base.py)).

### Graph repair
Background reconciliation that detects and fixes stale or orphaned nodes. Source: `jvagent/core/graph_repair.py`, `core/graph_repair_job.py`. Triggered on startup and via `/graph-repair` endpoints.

### Harness service tools
Tools the cockpit exposes to the model that wrap jvagent's internal services (memory, response, task, conversation, skill, artifacts). Always available regardless of which actions are enabled. See [`../docs/COCKPIT.md`](../docs/COCKPIT.md).

### Interaction
Single user-message ⇄ agent-response exchange. Source: `jvagent/memory/interaction.py:47`. Stores utterance, response, channel, actions taken, directives, events, parameters, usage, observability metrics. Bidirectionally chained to neighbor interactions.

### `interaction_limit`
The rolling-window size for a `Conversation`. Set on `Agent.interaction_limit` ([`agent.py:50`](../jvagent/core/agent.py)) and inherited by `Conversation.interaction_limit`. `0` disables pruning. See [`memory-and-pruning.md`](memory-and-pruning.md), [`adr/0003`](adr/0003-interaction-limit-pruning.md).

### `InteractAction`
Subclass of `Action` participating in the interact pipeline. Source: [`jvagent/action/interact/base.py:32`](../jvagent/action/interact/base.py). Implements `execute(walker)`. Has `weight`, `run_in_background`, `always_execute`, `anchors`, `parameters`.

### `InteractWalker`
jvspatial `Walker` subclass that drives the interact subsystem. Source: `jvagent/action/interact/interact_walker.py:50+`. Bootstraps `User` / `Conversation` / `Interaction` and visits each top-level `InteractAction` in `weight` order.

### `LanguageModelAction`
Subclass of `BaseModelAction` for LLM providers. Source: `jvagent/action/model/language/base.py:24`. Concrete subclasses: Anthropic, OpenAI, OpenRouter, Ollama.

### Memory (node)
Branchpoint node connecting an `Agent` to its `User` graph. One per agent. Source: `jvagent/memory/manager.py:18`.

### Namespace
Logical grouping for actions (e.g., `jvagent/`, `contrib/`, `custom/`). Prevents name collisions across packages. Combined with action_name for fully qualified `namespace/action_name`. See [`adr/0004`](adr/0004-namespace-isolation.md).

### Posture
Cockpit-router classification of an utterance: `RESPOND`, `SUPPRESS`, or `DEFER`. Drives whether the cockpit engages and which skills it loads. See [`../docs/COCKPIT.md`](../docs/COCKPIT.md).

### Profile (scaffold)
Built-in starter template for a new agent (`minimal`, `conversational`, `whatsapp_voice`, `research`). Selected via `jvagent app create --agent namespace/name@profile`. See `docs/scaffolding.md`.

### Pruning
The act of removing oldest `Interaction`s from a `Conversation` to keep it within `interaction_limit`. Capped per call by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100). See [`memory-and-pruning.md`](memory-and-pruning.md).

### Response bus
Per-agent message bus. Source: `jvagent/action/response/response_bus.py`. Channel adapters and filters register here. `InteractAction.publish()` writes to it.

### Root
The single jvspatial `Root` node. `App` is connected to `Root`.

### Skill
Markdown-first procedure (with optional Python tool script) the cockpit can load to instruct the model. Skills are discovered via `SkillCatalog` (`jvagent/action/cockpit/catalog/skill_catalog.py`). Distinct from `Action`s.

### Stuck detection
Heuristic in `CockpitEngine` that terminates the run when recent tool calls overlap too much (Jaccard similarity over the last `stuck_detection_window`). Defaults: window 4, threshold 0.65. Source: `cockpit_interact_action.py:187-190`.

### Task
A structured plan/step node created by `task_creation_interact_action` and dispatched by `task_dispatcher`. Persists on the `Conversation` or `Interaction` so long-running work survives restarts.

### Tool
A named callable wrapped with a JSON Schema for arguments, exposed to the cockpit LLM. Sources: `jvagent.tooling.tool.Tool`; built from `Action.get_tools()` for action tools, and from `cockpit/*_tools.py` for harness service tools.

### Update mode
The bootstrap intent for the next start: `run` (don't re-sync YAML), `merge` (non-destructive merge), or `source` (destructive sync). Stored on `App.update_mode` ([`app.py:74`](../jvagent/core/app.py)). CLI flags `--update --merge` / `--update --source` override per-process. See [`adr/0005`](adr/0005-app-yaml-agent-yaml-split.md).

### User (memory node)
Identity in the memory subgraph, scoped by `(memory_id, user_id)`. Source: `jvagent/memory/user.py:25`. Holds cross-session memory dict and tags. Distinct from the **admin user** (authentication subject) — those live in jvspatial's auth tables.

### Walker
jvspatial primitive that traverses the graph. `InteractWalker` is jvagent's main one. See jvspatial `SPEC.md` and [`jvspatial-integration.md`](jvspatial-integration.md).

### Walker-revisit pattern
The cockpit's iteration scheme: one model call per walker visit; on tool-call returns, persist state and `visitor.prepend([self])` to come back next visit. See [`adr/0002`](adr/0002-walker-revisit-cockpit.md).

### Weight
Integer attribute on `InteractAction` controlling top-tier execution order. Lower = earlier. Negative allowed (cockpit uses `-200`). Applies **only** to top-tier actions connected directly to the `Actions` node — sub-`InteractAction`s are traversed in graph arrangement order. Source: [`interact/base.py:64`](../jvagent/action/interact/base.py).
