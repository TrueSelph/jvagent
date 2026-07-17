# Glossary

Single-source definitions for terms used throughout jvagent. Whenever a defined term appears for the first time in another doc, it links here.

---

### Action
Pluggable component that extends an agent. Subclass of jvspatial `Node`. Source: [`jvagent/action/base.py:49`](../jvagent/action/base.py). Has lifecycle hooks (`on_register`, `on_enable`, etc.), attribute config, optional endpoints, optional tools (via `get_tools()`) for the agentic loop. Lives in a directory under `jvagent/action/{namespace}/{action_name}/`. See [`action-authoring.md`](reference/action-authoring.md).

### Actions (node)
Branchpoint node connecting an `Agent` to its `Action` children. One per agent. Accessed via `await agent.get_actions_manager()` ([`agent.py:126`](../jvagent/core/agent.py)).

### Agent
Logical execution unit. One graph node per agent; many per app. Source: [`jvagent/core/agent.py:30`](../jvagent/core/agent.py). Owns an `Actions` subgraph and a `Memory` subgraph.

### App
Singleton root node for the application. Source: [`jvagent/core/app.py:21`](../jvagent/core/app.py). Manages app-level settings (timezone, file storage, update mode) and is accessed as a singleton via `App.get()`.

### `app.yaml`
Declarative configuration file at the app root. Defines app-level settings: app_id, name, version, file storage, database backend, logging. Read by `app_loader`. See [`configuration-keys.md`](reference/configuration-keys.md).

### `agent.yaml`
Declarative configuration file per agent (under `agents/{namespace}/{agent_name}/agent.yaml`). Defines the agent's actions and per-action `context:` overrides. Read by `agent_loader`.

### Anchor
Short statement an `InteractAction` publishes to advertise when it should be selected. Defined as `anchors: List[str]` on `InteractAction` ([`interact/base.py`](../jvagent/action/interact/base.py)); can be dynamic via `get_anchors()`. Under Orchestrator, IAs surface via `get_tools()` — description built from manifest `purpose` + `activates_on` (`InteractAction.routing_triggers()`).

### Background action
An `InteractAction` with `run_in_background=True` ([`interact/base.py:81`](../jvagent/action/interact/base.py)). Queued by the walker and executed as a fire-and-forget asyncio task **after** the user-facing response is sent. Failures are isolated.

### `BaseModelAction`
Base class for any model-using action (LLM, embedding, etc.). Source: `jvagent/action/model/base.py:26`. Provides retry config (`max_retries`, `retry_backoff_multiplier`, etc.).

### `info.yaml`
Per-action package metadata file. Defines package name, archetype, version, group, dependencies (jvagent, other actions, pip packages), and default config. Read at action register/load time. Stored on the action as `metadata`.

### Channel adapter
Component that translates response-bus messages to a specific transport (WhatsApp, Messenger, email, SSE, stdout). Registered with the agent's `ResponseBus`. Lives in `jvagent/action/response/channel_adapter.py` and per-channel actions.

### Conversation
Session-keyed node holding a chained sequence of `Interaction` nodes. Source: `jvagent/memory/conversation.py:39`. Created lazily by `Memory.get_user()`. May override `Agent.interaction_limit`.

### `DeferredSaveMixin`
jvspatial-provided mixin used by `Conversation` and `Interaction` to batch writes for efficiency. Flushed after the walker completes.

### Endpoint
HTTP route defined by the `@endpoint` decorator from jvspatial. Actions ship endpoints in `endpoints.py`. Registered at server bootstrap; auto-cleaned on action deregister ([`base.py:403`](../jvagent/action/base.py)).

### Orchestrator
The single orchestrator of the Orchestrator pattern. Implemented by [`OrchestratorInteractAction`](../jvagent/action/orchestrator/orchestrator_interact_action.py) at weight `-200`. Runs the whole turn inside one `execute()` call: when a turn-spanning flow has an active control-task on the conversation `TaskStore` it surfaces that flow as a tool and notes it, then runs a bounded think-act-observe loop over a unified tool surface (routing is tool selection) in which the model decides whether to continue the flow. No recruited centers, no reflex, no separate router. Supersedes the Executive + Centers pattern. See [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md), [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md), [`PATTERNS.md`](PATTERNS.md).

### Flow continuation
The mechanism by which a turn-spanning flow keeps (or releases) the turn under the Orchestrator. A *flow* records a control-task on the conversation `TaskStore` while active (the flow manages this itself). Each turn the Orchestrator detects it via `active_flow_owner(visitor)` (the active task's `owner_action`, equal to the flow's tool name). The mode is set by `lock_active_flow` ([ADR-0013](adr/0013-togglable-deterministic-turn-lock.md)): when **True** (default) the loop restricts its callable surface to that flow's IA tool and dispatches it with no model round-trip (mechanistic turn-lock); when **False** it surfaces the flow's tool and injects `active_flow_note(tool_name)`, and the model either continues the flow (selecting its tool, whose `get_tools` → `execute` advances the session) or routes an off-topic request elsewhere (the flow is not forced to run, its control-task persists, and it resumes when the user returns). In neither mode does the flow gain a resume entry point or outcome enum — its only orchestrator-facing surface is `get_tools()`. Source: [`jvagent/action/orchestrator/continuation.py`](../jvagent/action/orchestrator/continuation.py). See [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md).

### Graph repair
Background reconciliation that detects and fixes stale or orphaned nodes. Source: `jvagent/core/graph_repair.py`, `core/graph_repair_job.py`. Triggered on startup and via `/graph-repair` endpoints.

### Interaction
Single user-message ⇄ agent-response exchange. Source: `jvagent/memory/interaction.py:51`. Stores utterance, response, channel, actions taken, directives, events, parameters, usage, observability metrics. Bidirectionally chained to neighbor interactions.

### `interaction_limit`
The rolling-window size for a `Conversation`. Set on `Agent.interaction_limit` ([`agent.py:72`](../jvagent/core/agent.py)) and inherited by `Conversation.interaction_limit`. `0` disables pruning. See [`memory-and-pruning.md`](reference/memory-and-pruning.md), [`adr/0003`](adr/0003-interaction-limit-pruning.md).

### `InteractAction`
Subclass of `Action` participating in the interact pipeline. Source: [`jvagent/action/interact/base.py:27`](../jvagent/action/interact/base.py). Implements `execute(walker)`. Has `weight`, `run_in_background`, `always_execute`, `anchors`, `parameters`.

### `InteractRouter` (removed 0.1.1)
Former Rails-pattern router (weight `-200`). Removed in favor of `OrchestratorInteractAction`. See ADR-0029.

### `InteractWalker`
jvspatial `Walker` subclass that drives the interact subsystem. Source: `jvagent/action/interact/interact_walker.py:47+`. Bootstraps `User` / `Conversation` / `Interaction` and visits each top-level `InteractAction` in `weight` order.

### `LanguageModelAction`
Subclass of `BaseModelAction` for LLM providers. Source: `jvagent/action/model/language/base.py:345`. Concrete subclasses: Anthropic, OpenAI, OpenRouter, Ollama.

### `latency_class`
Manifest field on an `Action` package declaring expected wall-clock cost: `instant`, `quick` (sub-2s), `deliberate` (2–10s), `long` (>10s). Used by the orchestrator to decide whether activating a slow target warrants an ack lead-in. Source: [`jvagent/action/manifest.py`](../jvagent/action/manifest.py).

### Manifest
Pattern-agnostic metadata block on an `Action` package descriptor. Declares `purpose`, `activates_on`, `terminates_when`, `latency_class`, `turn_lock`, `interrupt_phrases`, `can_interrupt`, `expected_duration_seconds`, `routable_by_anchor`, `pattern_orchestrator`. Read at package load via `Action.get_manifest()`. Consumed by the Orchestrator. Source: [`jvagent/action/manifest.py`](../jvagent/action/manifest.py).

### Memory (node)
Branchpoint node connecting an `Agent` to its `User` graph. One per agent. Source: `jvagent/memory/manager.py:18`.

### Namespace
Logical grouping for actions (e.g., `jvagent/`, `contrib/`, `custom/`). Prevents name collisions across packages. Combined with action_name for fully qualified `namespace/action_name`. See [`adr/0004`](adr/0004-namespace-isolation.md).

### Pattern orchestrator
An InteractAction marked with `manifest.pattern_orchestrator: true` (today: `OrchestratorInteractAction`). Weight-routed at `-200`; never anchor-routed. Pattern orchestrators are excluded from the tool surface so a stray tool selection cannot recurse into the orchestrator.

### `pattern_orchestrator`
Boolean field on `Manifest` (default `False`). Marks an InteractAction as a pattern orchestrator (today: the Orchestrator). Pattern orchestrators are weight-routed and excluded from the tool surface.

### Posture
Legacy classification of an utterance: `RESPOND`, `SUPPRESS`, or `DEFER`. Optional field on `Interaction.response_posture` for historical-turn observability (Rails pattern removed 0.1.1).

### Proactive message / Proactive interaction
An `Interaction` recorded in conversation history that originates from the agent (or owning code) without an inbound user utterance. Shape: `utterance == ""`, `response == <agent text>`, tagged via `Interaction.parameters` with `{"is_proactive": True, "action_name": <source>, ...metadata}`. Sent programmatically via [`Agent.send_proactive_message`](../jvagent/core/agent.py) ([`agent.py:271-358`](../jvagent/core/agent.py)) — see [`../docs/proactive-messages.md`](../docs/proactive-messages.md). In LLM history serialization the empty-utterance `user` role is suppressed ([`conversation.py:741-752`](../jvagent/memory/conversation.py)), so the entry shows as a standalone `assistant` turn.

### Profile (scaffold)
Built-in starter template for a new agent (`orchestrator`, `minimal`, `conversational`, `whatsapp_voice`, `research`). Selected via `jvagent app create --profile profile_name`. Default is `orchestrator`. See `docs/scaffolding.md`.

### Pruning
The act of removing oldest `Interaction`s from a `Conversation` to keep it within `interaction_limit`. Capped per call by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100). See [`memory-and-pruning.md`](reference/memory-and-pruning.md).

### `routable_by_anchor`
Boolean field on `Manifest` (default `True`). When `False`, the IA is excluded from anchor-routing surfaces — it is reachable only via explicit activation by a parent flow. See [`action-authoring.md`](reference/action-authoring.md).

### Response bus
Per-agent message bus. Source: `jvagent/action/response/response_bus.py`. Channel adapters and filters register here. `InteractAction.publish()` writes to it.

### Root
The single jvspatial `Root` node. `App` is connected to `Root`.

### Session capability token
Server-minted HS256 JWT that authorizes resuming one web `Conversation` on the public interact endpoint (ADR-0020 "Mode B"). Claims bind it to `agent_id`/`session_id`/`user_id` plus a per-conversation `cs` secret (`Conversation.token_secret` — rotating it revokes all outstanding tokens). Minted/re-minted each interact turn (`session_token` response field / SSE `start` chunk, resent as `X-Session-Token`); renewable without an utterance via `POST /agents/{id}/interact/session/refresh` within a post-expiry grace window (ADR-0032). Source: [`jvagent/action/interact/session_token.py`](../jvagent/action/interact/session_token.py).

### Skill
A `SKILL.md`-first folder the Orchestrator loads through progressive disclosure (`find_skill` / `use_skill`, [`jvagent/action/orchestrator/skills.py`](../jvagent/action/orchestrator/skills.py)). A skill is *judgment over capability* — distinct from `Action`s. Two specs (ADR-0017), set by a `spec` frontmatter key: **JV** (`spec: jv`, default) coordinates existing action/IA tools by `namespace__tool` name (no code); **Claude** (`spec: claude`) is a standard Anthropic Agent Skills folder whose bundled `scripts/` the model runs via the `code_execution__bash` substrate, staged into the caller's per-user sandbox. See [`adr/0017-two-skill-specs-code-execution-substrate.md`](adr/0017-two-skill-specs-code-execution-substrate.md), [`adr/0011-skills-two-kinds.md`](adr/0011-skills-two-kinds.md), and [`jvagent/skills/README.md`](../jvagent/skills/README.md).

### InterviewAction
`Action` (not `InteractAction`) that registers `interview__*` tools and runs the interview session pipeline for skills with `interview:` frontmatter and `extends: action:jvagent/interview`. Source: [`jvagent/action/interview/`](../jvagent/action/interview/). Follows the *thin harness* design — session + hooks + raw tools only; the model drives turns via the composed SOP.

### Conversation Use Case Specification (CUCS)
Portable YAML format (`schema: jvagent.use-case/v1`) for documenting multi-turn conversational scenarios that inform orchestrator E2E test suites. Normative spec: [`.planning/reference/conversation-use-cases.md`](reference/conversation-use-cases.md). JSON Schema: [`jvagent/schemas/use-case-v1.schema.json`](../jvagent/schemas/use-case-v1.schema.json). ADR: [`adr/0027`](adr/0027-conversation-use-case-spec.md). Distinct from `SKILL.md` `interview:` frontmatter (implementation contract vs. behavioral scenario). Witness: [`example_account_gating/use-cases/`](../jvagent/action/interview/examples/example_account_gating/use-cases/).

### Fixture profile
Named reusable stub or seed configuration referenced from a CUCS scenario's `given.fixtures` block. App-defined (e.g. zoon `fixtures.stub: profile/default-unregistered`). Documented in the app's `docs/use-cases.md`.

### Harness (CUCS)
The `harness.decisions` block in a CUCS turn — canned `_run_model` returns that drive deterministic orchestrator E2E tests. Maintained by engineers; not required for product/QA review of `when`/`then`.

### Scenario (CUCS)
One YAML file describing a single use case: metadata, `given` world state, ordered `turns`, optional `outcome`. Lives under `<app-root>/use-cases/`.

### Use case (CUCS)
A documented multi-turn user journey across one or more skills, expressed as a CUCS scenario. Informs orchestrator E2E tests; does not replace handler unit tests or `interview:` field contracts.

### Assertion namespace (CUCS)
Grouped `then` keys evaluated by the test runner. Framework-owned: `task_graph`, `context`, `session`, `publish`, `tools_surface`. App extensions (e.g. `api`) documented per app.

### Thin harness (principle)
jvagent-wide design contract: **thin server harness** (Orchestrator, Actions, turn-lock hooks — session, validation dispatch, raw tool JSON, no conversation steering) and **thick SOP + skill extensions** (intent routing, extraction, domain logic in skills and `custom_tools.py`). Forbidden platform-wide: server intent classification, prep steering, activation auto-store, response inlining, orchestrator action special-casing. Canonical rules: [`docs/thin-harness.md`](../docs/thin-harness.md). Interview-specific profile: [`jvagent/action/interview/docs/thin-harness.md`](../jvagent/action/interview/docs/thin-harness.md).

### Sustained activation (turn-lock)
A flow declaring it owns the turn across user messages. Realized as **flow continuation**, whose mode is set by `lock_active_flow` (ADR-0013): the flow records a control-task on the conversation's declarative `TaskStore` while active, and each turn the Orchestrator detects it. When `lock_active_flow=True` (default) the orchestrator routes the turn deterministically to the flow's IA (mechanistic turn-lock); when `False` it surfaces the flow as a tool and the model decides whether to continue it or route an off-topic request elsewhere (the control-task persists and resumes later). See *Flow continuation* and [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md).

### Task
A structured plan/step node created by `task_creation_interact_action` and dispatched by `task_monitor`, or a declarative record in the conversation `TaskStore`. Persists on the `Conversation` or `Interaction` so long-running work survives restarts.

### Tool
A named callable wrapped with a JSON Schema for arguments, exposed to the model. Source: `jvagent.tooling.tool.Tool`; built from `Action.get_tools()` for action tools.

### Tool catalog
A slim index of the available tools that the Orchestrator carries in the prompt instead of every tool schema, with `find_tool` / `load_tool` meta-tools for progressive disclosure — bounding prompt size as the tool surface grows (mirrors the skills `find_skill` / `use_skill` meta-tools). Source: [`jvagent/action/orchestrator/catalog.py`](../jvagent/action/orchestrator/catalog.py).

### Turn-lock
Declares that an `InteractAction` owns the next turn end-to-end (e.g. interview, form, gated workflow). Under the Orchestrator turn-lock is realized as **flow continuation**, configurable via `lock_active_flow` (ADR-0013): the flow records a control-task on the conversation `TaskStore` while active. When `lock_active_flow=True` (default) the loop restricts its callable surface to the flow's IA tool and dispatches it with no model round-trip — a mechanistic lock; the IA owns interruption/cancel. When `False` the flow is surfaced as a tool and the lock is emergent — the model continues the flow by selecting its tool or routes an off-topic message elsewhere (interruptibility automatic). In both modes the flow's own session logic handles its steps. See *Sustained activation (turn-lock)* and *Flow continuation*.

### Update mode
The bootstrap intent for the next start: `run` (don't re-sync YAML), `merge` (non-destructive merge), or `source` (destructive sync). Stored on `App.update_mode` ([`app.py:76`](../jvagent/core/app.py)). CLI flags `--update --merge` / `--update --source` override per-process. See [`adr/0005`](adr/0005-app-yaml-agent-yaml-split.md).

### User (memory node)
Identity in the memory subgraph, scoped by `(memory_id, user_id)`. Source: `jvagent/memory/user.py:25`. Holds cross-session memory dict and tags. Distinct from the **admin user** (authentication subject) — those live in jvspatial's auth tables.

### Walker
jvspatial primitive that traverses the graph. `InteractWalker` is jvagent's main one. See jvspatial `SPEC.md` and [`jvspatial-integration.md`](reference/jvspatial-integration.md).

### Weight
Integer attribute on `InteractAction` controlling top-tier execution order. Lower = earlier. Negative allowed (the Orchestrator uses `-200`). Applies **only** to top-tier actions connected directly to the `Actions` node — sub-`InteractAction`s are traversed in graph arrangement order. Source: [`interact/base.py:59`](../jvagent/action/interact/base.py).

### Loop state (Orchestrator)
The Orchestrator's per-turn state for the think-act-observe loop: the accumulated observations, the tool-call transcript, and the activation/model budget. It is transient and cleared after `execute()` returns. Turn-lock is NOT held here — a flow's control-task lives on the conversation `TaskStore` (see *Flow continuation*). Source: [`jvagent/action/orchestrator/orchestrator_interact_action.py`](../jvagent/action/orchestrator/orchestrator_interact_action.py).
