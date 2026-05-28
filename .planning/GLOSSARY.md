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

### Bridge
The multi-helm deployment pattern. Implemented by [`BridgeInteractAction`](../jvagent/action/bridge/bridge_interact_action.py) at weight `-200`. Composes N helms (Reflex / Reasoning / Persona / Specialist via DELEGATE) behind one agent slot; helms shift between each other via the `HelmStepResult` verb set. Peer pattern to Cockpit; one or the other, never both, per agent. See [`../docs/BRIDGE.md`](../docs/BRIDGE.md), [`adr/0007`](adr/0007-bridge-helm-architecture.md), [`PATTERNS.md`](PATTERNS.md).

### `BridgeState`
Per-run state persisted on `visitor._bridge_state` between walker visits (parallel to `CockpitState` on `visitor._skill_state`). Fields: current helm, shift budget remaining, shift_log, helm_timings_seconds, helm_step_counts, ack-emitted flags. Source: [`jvagent/action/bridge/state.py`](../jvagent/action/bridge/state.py).

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

### Helm
A subclass of `BaseHelm` (which extends `Action`) participating in a Bridge composition. Implements `step(visitor, bridge_state) → HelmStepResult`. Helms are orchestrated by `BridgeInteractAction`; they never touch the walker queue directly. Source: [`jvagent/action/helm/base.py`](../jvagent/action/helm/base.py). See [`../docs/BRIDGE.md`](../docs/BRIDGE.md).

### `HelmStepResult` (verb set)
The closed enum of return values from `Helm.step()` that Bridge dispatches: `EMIT`, `CONTINUE`, `SHIFT`, `DELEGATE`, `YIELD`. Current revision v0.2. History: `CONTINUE` joined at v0.1 (additive); `EXECUTE` and `SHIFT.interrupt` removed at v0.2 (breaking — both were unused). Source: [`jvagent/action/helm/contracts.py`](../jvagent/action/helm/contracts.py).

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

### `latency_class`
Manifest field on an `Action` package declaring expected wall-clock cost: `fast` (sub-500ms), `quick` (sub-2s), `deliberate` (2–10s), `long` (>10s). Used by Bridge to decide whether `SHIFT` to that target needs an ack-on-shift. Source: [`jvagent/action/manifest.py`](../jvagent/action/manifest.py).

### Manifest
Pattern-agnostic metadata block on an `Action` package descriptor. Declares `purpose`, `activates_on`, `terminates_when`, `latency_class`, `turn_lock`, `interrupt_phrases`, `expected_duration_seconds`, plus pattern-specific extensions. Read at package load via `Action.get_manifest()`. Consumed by Bridge for orchestration; informational for Cockpit / Rails. Source: [`jvagent/action/manifest.py`](../jvagent/action/manifest.py).

### Memory (node)
Branchpoint node connecting an `Agent` to its `User` graph. One per agent. Source: `jvagent/memory/manager.py:18`.

### Namespace
Logical grouping for actions (e.g., `jvagent/`, `contrib/`, `custom/`). Prevents name collisions across packages. Combined with action_name for fully qualified `namespace/action_name`. See [`adr/0004`](adr/0004-namespace-isolation.md).

### Posture
Cockpit-router classification of an utterance: `RESPOND`, `SUPPRESS`, or `DEFER`. Drives whether the cockpit engages and which skills it loads. See [`../docs/COCKPIT.md`](../docs/COCKPIT.md).

### Proactive message / Proactive interaction
An `Interaction` recorded in conversation history that originates from the agent (or owning code) without an inbound user utterance. Shape: `utterance == ""`, `response == <agent text>`, tagged via `Interaction.parameters` with `{"is_proactive": True, "action_name": <source>, ...metadata}`. Sent programmatically via [`Agent.send_proactive_message`](../jvagent/core/agent.py) ([`agent.py:226-319`](../jvagent/core/agent.py)) — see [`../docs/proactive-messages.md`](../docs/proactive-messages.md). In LLM history serialization the empty-utterance `user` role is suppressed ([`conversation.py:553-566`](../jvagent/memory/conversation.py)), so the entry shows as a standalone `assistant` turn.

### PersonaHelm (scrapped)
Historical term — the originally-planned Bridge helm wrapping `PersonaAction`. Scrapped in May 2026 (see [`adr/0007`](adr/0007-bridge-helm-architecture.md) accepted-state amendments). Persona stylisation in Bridge mode is now invoked by helms via `EMIT(via_persona=True)` which routes through `BridgeInteractAction._publish_emit_via_persona` → `PersonaAction.respond`. Functionally equivalent without consuming a shift-budget slot. The `jvagent/action/helm/persona/` package no longer exists.

### Profile (scaffold)
Built-in starter template for a new agent (`minimal`, `cockpit`, `bridge`, `conversational`, `whatsapp_voice`, `research`). Selected via `jvagent app create --profile profile_name`. See `docs/scaffolding.md`.

### Pruning
The act of removing oldest `Interaction`s from a `Conversation` to keep it within `interaction_limit`. Capped per call by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100). See [`memory-and-pruning.md`](memory-and-pruning.md).

### ReasoningHelm
Bridge helm running a cockpit-style think-act-observe loop. Independent duplication of cockpit's engine under `jvagent/action/helm/reasoning/` — zero source-level imports from `cockpit/`. Source: [`jvagent/action/helm/reasoning/reasoning_helm.py`](../jvagent/action/helm/reasoning/reasoning_helm.py). See [`../docs/BRIDGE.md`](../docs/BRIDGE.md).

### ReflexHelm
Bridge helm running a sub-500ms classifier on a fast completion model. Handles trivial turns (greetings, smalltalk, simple acknowledgements) directly via `EMIT`; SHIFTs to a deliberate helm for any utterance requiring reasoning. Owns the user-facing canned / ack-on-shift in Bridge compositions. Source: [`jvagent/action/helm/reflex/reflex_helm.py`](../jvagent/action/helm/reflex/reflex_helm.py).

### Response bus
Per-agent message bus. Source: `jvagent/action/response/response_bus.py`. Channel adapters and filters register here. `InteractAction.publish()` writes to it.

### Root
The single jvspatial `Root` node. `App` is connected to `Root`.

### Skill
Markdown-first procedure (with optional Python tool script) the cockpit can load to instruct the model. Skills are discovered via `SkillCatalog` (`jvagent/action/cockpit/catalog/skill_catalog.py`). Distinct from `Action`s.

### `ShiftRecord`
One entry in `BridgeState.shift_log`. Emitted for the initial helm pick (`routing_source="initial"`), every `SHIFT` verb (`"helm_shift"`), every `DELEGATE` verb (`"helm_delegate"`), and every turn-lock auto-DELEGATE (`"turn_lock"`). Persisted as part of the `Interaction.parameters["bridge_observability"]` bundle. Fields: `from_helm`, `to_helm`, `reason`, `ack_emitted`, `shift_index`, `at_monotonic`, `handoff_state`, `routing_source`. Source: [`jvagent/action/helm/contracts.py`](../jvagent/action/helm/contracts.py).

### Specialist (Bridge)
Not a helm — a rails `InteractAction` invoked via `DELEGATE(action=...)`. Lets Bridge yield cleanly to a deterministic IA for an in-progress workflow (interview, form, gated process). AccessControl gated by `tool:delegate:{action_name}`. See [`../docs/BRIDGE.md`](../docs/BRIDGE.md).

### Stuck detection
Heuristic in `CockpitEngine` that terminates the run when recent tool calls overlap too much (Jaccard similarity over the last `stuck_detection_window`). Defaults: window 4, threshold 0.65. Source: `cockpit_interact_action.py:187-190`.

### Task
A structured plan/step node created by `task_creation_interact_action` and dispatched by `task_dispatcher`. Persists on the `Conversation` or `Interaction` so long-running work survives restarts.

### Tool
A named callable wrapped with a JSON Schema for arguments, exposed to the cockpit LLM. Sources: `jvagent.tooling.tool.Tool`; built from `Action.get_tools()` for action tools, and from `cockpit/*_tools.py` for harness service tools.

### Turn-lock
Manifest flag (`turn_lock: true`) declaring that an `InteractAction` owns the next turn end-to-end (e.g. interview, form, gated workflow). Bridge scans the recent interaction history via `find_turn_lock_owner()`; when a lock owner is mid-workflow, the next utterance is **always** auto-`DELEGATE`d to it — there is no helm-level escape. Lock-breaking lives in the lock-owning IA's own intent classifier (e.g. an interview's CANCELLATION intent reading `manifest.interrupt_phrases`). Source: [`jvagent/action/bridge/turn_lock.py`](../jvagent/action/bridge/turn_lock.py).

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
