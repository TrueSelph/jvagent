# Cockpit Module Specification (Revised)

## Overview

`action/cockpit/` is a self-contained InteractAction module that transforms the agent harness into a **model cockpit** — a flight deck where the language model is the pilot and every harness service, action tool, and skill capability is presented as a coherent set of instruments (tools) it can invoke. The cockpit replaces scripted action chains with a think‑act‑observe loop: the model surveys the situation, decides which tools to engage, observes the results, and continues until the mission is complete.

When a user message arrives, the cockpit’s `CockpitInteractAction` is one of the actions visited by the `InteractWalker`. It first routes the message (posture + skill selection) and then — for non‑conversational intents — runs a multi‑step engine loop where the model has first‑class access to services such as **session‑scoped memory (including artifact CRUD), persona‑driven response delivery, task planning, a unified search across skills/actions/tools, and all domain‑specific actions registered on the agent**.

Every service is exposed as a **tool** that the model can discover and call, allowing it to efficiently execute everything from a single‑operation lookup to a complex, multi‑step workflow.

The cockpit retains the walker‑revisit pattern of the original harness but provides a far more intelligent, token‑efficient, and predictable execution model — exactly the rail‑like, directed intelligence we have been aiming for.

---

## Self-Containment Constraint

The cockpit module imports **only** from:

- `jvagent.tooling.*` (Tool, ToolRegistry, ToolExecutionEngine, ToolSerializer, ToolResult)
- `jvagent.action.interact.base` (InteractAction base class)
- `jvagent.action.model.language.base` (LanguageModelAction, used via duck-typing at runtime)
- `jvagent.core.*` (app_context, cache, scaffold skill resolution)
- Standard library

It has **zero** imports from `jvagent.action.skill`, `jvagent.action.router`, or `jvagent.action.persona`. Utilities that would create circular dependencies (e.g., `version_satisfies`) are inlined locally. PersonaAction is accessed via duck‑typing (`hasattr(persona, "respond_slim")`) and `self.get_action("PersonaAction")`.

---

## Architecture

### File Inventory (24 files)

| File | Role |
|---|---|
| `__init__.py` | Public API re‑exports (17 symbols) |
| `cockpit_interact_action.py` | Main InteractAction entry point |
| `engine.py` | Think‑act‑observe engine (one model call per step) |
| `config.py` | CockpitConfig dataclass |
| `context.py` | CockpitContext, CockpitStepResult, CockpitResult, CockpitState |
| `contracts.py` | TerminationReason enum |
| `router.py` | CockpitRouter (Phase 1 lightweight LLM routing) |
| `routing_types.py` | Posture constants, RoutingResult, parse/format utilities |
| `gates.py` | Conversational vs processing gate decisions |
| `delivery.py` | Response delivery (conversational + final response) |
| `registry.py` | Tool assembly (harness + action + skill layers) |
| `action_resolver.py` | ActionResolver + inlined version_satisfies |
| `skill_catalog.py` | SkillCatalog (discovery, rendering, search) |
| `skill_discovery.py` | Always‑active skill detection |
| `skill_tools.py` | skill_list, skill_search, skill_read harness tools |
| `task_tools.py` | task_create_plan, task_update_step, task_get_status, task_add_step |
| `memory_tools.py` | memory_get_history, memory_get_user_info, memory_update_user_model, memory_set_preference |
| `artifact_tools.py` | artifact_search, artifact_add, artifact_get, artifact_update, artifact_delete (session‑scoped artifact CRUD) |
| `search_tools.py` | cockpit_search — unified search across skills, actions, and tools |
| `response_tools.py` | response_publish, response_emit_thought, response_deliver_via_persona |
| `conversation_tools.py` | conversation_search, conversation_summarize |
| `shim.py` | CockpitVisitorShim (minimal visitor stand‑in) |

### Dependency Graph

```
CockpitInteractAction
  ├── CockpitRouter ──► routing_types, SkillCatalog, CockpitVisitorShim
  ├── CockpitEngine ──► CockpitContext, ToolExecutionEngine, ToolRegistry, ToolSerializer
  │       └── registry.assemble_cockpit_tools
  │             ├── _build_memory_tools    (CockpitContext)
  │             ├── _build_artifact_tools  (CockpitContext)
  │             ├── _build_search_tools    (CockpitContext)     ← new unified search
  │             ├── _build_response_tools  (CockpitContext)
  │             ├── _build_task_tools       (CockpitContext)
  │             ├── _build_conversation_tools (CockpitContext)
  │             ├── _build_skill_tools      (CockpitContext)
  │             ├── _register_action_tools  (agent → ActionsManager)
  │             └── _register_skill_tools   (SkillCatalog, ActionResolver, dynamic loading)
  ├── gates ──► routing_types
  ├── delivery ──► CockpitResult, SkillCatalog
  └── skill_discovery ──► SkillCatalog, CockpitVisitorShim
```

---

## Two-Phase Execution Model

### Phase 1: Route + Gate (`_phase_route_and_setup`)

When the `InteractWalker` first visits `CockpitInteractAction.execute()`:

1. **Stale‑state guard**: Check `visitor._skill_state["cockpit_interaction_id"]` against the current `interaction.id`. If they differ, clear all cockpit state keys and re‑route.

2. **Routing**: `CockpitRouter.route()` makes a lightweight LLM call to classify:
   - **Posture**: `RESPOND` (handle normally), `SUPPRESS` (ignore silently), `DEFER` (delegate to cockpit)
   - **Intent type**: `CONVERSATIONAL`, `INFORMATIONAL`, `DIRECTIVE`, `INTERACTIVE`, `UNCLEAR`
   - **Recommended skills**: List of skill names from the catalog
   - **Confidence + interpretation**

3. **Posture dispatch**:
   - `SUPPRESS` → return immediately (no response)
   - `DEFER` or `RESPOND` → continue to gating

4. **Gating** (`should_use_conversational_gate`):
   - `CONVERSATIONAL` intent or no recommended skills → conversational gate (PersonaAction‑only brief reply)
   - `INFORMATIONAL` or `DIRECTIVE` intent with skills → processing gate (full cockpit engine)

5. **Cockpit setup** (`_start_cockpit`):
   - Build `CockpitContext` with all runtime references
   - Create `CockpitEngine`, call `initialize()`, run first `step()`
   - Persist engine instance + interaction ID on `visitor._skill_state`

### Phase 2: Think‑Act‑Observe Loop (`_phase_continue`)

On subsequent walker revisits:

1. Retrieve engine from `visitor._skill_state["cockpit_engine"]`
2. Call `engine.step()` — executes one model call
3. Handle the `CockpitStepResult`:
   - `tool_calls` → persist state, `visitor.prepend([self])` to revisit
   - `final_response` / terminal → deliver response, clear state, conclude

---

## Walker‑Revisit Pattern

The cockpit does **not** use an internal `while` loop. Instead:

1. Each call to `CockpitEngine.step()` makes exactly one model call.
2. If the model returns tool calls, `CockpitInteractAction._handle_step_result()` calls `visitor.prepend([self])` to re‑add itself to the walk path.
3. The `InteractWalker` revisits the action on the next walk cycle.
4. `execute()` detects the existing engine instance in `_skill_state` and skips routing, going directly to `_phase_continue()`.
5. `_phase_continue()` calls `step()` again, continuing the loop.

This means the cockpit yields control back to the walker between each model call, allowing other actions in the pipeline to execute if needed.

### State Persistence Across Revisits

Three keys in `visitor._skill_state`:

| Key | Value | Purpose |
|---|---|---|
| `cockpit_engine` | `CockpitEngine` instance | Reused across visits (not serialized) |
| `cockpit_state` | `CockpitState` snapshot | Observability/debugging only |
| `cockpit_interaction_id` | `str` | Stale‑state guard — if current interaction ID differs, clear all state |

The `CockpitEngine` instance itself is persisted, not serialized/deserialized. All mutable state (messages, iteration counter, tool name history) lives on the engine object and survives across revisits because it's the same object.

---

## CockpitEngine

### Initialization (`initialize()`)

1. `assemble_cockpit_tools(ctx)` → `ToolRegistry` with all harness, action, and skill tools.
2. `ToolExecutionEngine(registry, call_timeout, max_concurrent, sanitize_errors)`
3. `ToolSerializer.serialize_all(registry.list())` → OpenAI‑compatible tool schemas
4. Build system prompt (see System Prompt section)
5. Build conversation history from `ctx.conversation.get_interaction_history()`
6. Append user message (`ctx.utterance`)

### Step Execution (`step()`)

```
step() → CockpitStepResult
  ├─ Budget checks (time, iteration count)
  ├─ model_action.query_messages(messages, tools=tools_serialized)
  ├─ If tool_calls:
  │    ├─ Dispatch ALL tool calls via ToolExecutionEngine
  │    ├─ Append assistant message + tool result messages
  │    ├─ Track tool names for stuck detection
  │    ├─ Check cockpit_finalized flag → return completed
  │    ├─ Check all‑errors → return error response
  │    ├─ Check stuck detection → return stuck response
  │    └─ Return status="tool_calls"
  └─ If text only:
       └─ Return status="final_response" with response text
```

**Dispatch‑before‑check ordering**: Tool calls are dispatched BEFORE checking the finalized flag. This ensures that when `response_publish(finalize=true)` is in a batch with other tools, all side effects execute. The finalized check happens after dispatch — if set, the loop concludes even though tool calls were made.

### Termination Conditions

| Condition | Status | TerminationReason | Response |
|---|---|---|---|
| Model produces text (no tool calls) | `final_response` | `COMPLETED` | Model's text |
| `response_publish(finalize=true)` called | `final_response` | `COMPLETED` | "" (already published) |
| Time budget exceeded | `timeout` | `TIME_CAP` | Fallback message |
| Iteration count exceeded | `budget_exhausted` | `ITER_CAP` | Fallback message |
| Stuck detection triggered | `stuck` | `STUCK` | "I seem to be making the same actions..." |
| All tool calls in batch failed | `final_response` | `ERROR` | Error details |

### Stuck Detection (`_check_stuck()`)

Two independent checks:

1. **Jaccard similarity** on tool‑name sets across a sliding window (default 3 iterations). If all adjacent pairs have Jaccard >= threshold (default 0.5), the model is stuck.

2. **Primary tool repeat**: If the same primary (first) tool is called in N consecutive iterations (default 4), the model is stuck.

Both checks use `CockpitConfig.stuck_detection_window`, `stuck_intent_jaccard_threshold`, and `stuck_primary_tool_repeat`.

---

## System Prompt

The system prompt is built from `COCKPIT_SYSTEM_PROMPT` with five placeholders:

| Placeholder | Source | Condition |
|---|---|---|
| `{agent_name}` | `persona.persona_name` | Always |
| `{agent_description}` | `persona.persona_description` + router guidance | Always; router guidance appended when skills recommended |
| `{task_planning}` | `TASK_PLANNING_BLOCK` | When `config.plan_first = True` |
| `{skill_index}` | SkillCatalog rendering | When skills are discovered |
| `{capability_search_note}` | Brief instruction to use `cockpit_search` | When the unified search tool is enabled and catalog size is large |

### Tool‑Use Cycle Instructions

The prompt explicitly instructs the model about the multi‑turn tool‑use cycle:

- Output ONLY tool calls when using tools — no accompanying text
- Tool results arrive in the next turn
- Continue calling tools until done
- Output final text response (no tool calls) when ready
- Call `response_publish(finalize=true)` to end early

### Task Planning Instructions

When `plan_first=True`, the prompt includes the `# Task planning` section:

- Create a plan first using `task_create_plan` with numbered steps
- Before each step, call `task_update_step` with status `in_progress`
- After completing a step, call `task_update_step` with status `done`
- If a step fails, call `task_update_step` with status `failed`
- Use `task_get_status` to review progress

### Skill Index Rendering

If the catalog has skills and some are preloaded:
- Filter to only preloaded skills, render inline

If the catalog has skills but none are preloaded:
- If skill count <= `skill_index_inline_max_skills` (default 5): render full catalog inline
- If skill count exceeds limit and `enable_skill_helper_tools=True`: render search‑mode prompt ("Use skill_search to find skills, or use cockpit_search for any capability")
- Otherwise: no skill section

### Router Guidance

When routing recommends specific skills, a `[Router guidance]` block is appended to `agent_description`:

```
[Router guidance] Intent: DIRECTIVE. Interpretation: User wants... Recommended skill(s): web_search, pageindex_docs. Use the available tools to address this request.
```

---

## Tool Architecture

### Three‑Layer Assembly (`assemble_cockpit_tools`)

```
ToolRegistry
  ├── [harness] prefix — Harness service tools
  │     ├── memory_get_history
  │     ├── memory_get_user_info
  │     ├── memory_update_user_model
  │     ├── memory_set_preference
  │     ├── artifact_search
  │     ├── artifact_add
  │     ├── artifact_get
  │     ├── artifact_update
  │     ├── artifact_delete
  │     ├── cockpit_search          (unified search)
  │     ├── response_publish
  │     ├── response_emit_thought
  │     ├── response_deliver_via_persona
  │     ├── task_create_plan
  │     ├── task_update_step
  │     ├── task_get_status
  │     ├── task_add_step
  │     ├── conversation_search
  │     ├── conversation_summarize
  │     ├── skill_list
  │     ├── skill_search
  │     └── skill_read
  ├── [action] prefix — Tools from agent's ActionsManager
  │     └── (dynamically collected via get_all_tools())
  └── [skill_name] prefix — Tools from skill bundle directories
        └── (dynamically loaded from .py files via importlib)
```

### Harness Tools

Built by factory functions (`_build_*_tools(ctx)`) that close over `CockpitContext`. Each returns `List[Tool]`. All inner functions are `async def` and return `str` — `Tool.call()` converts `str` to `ToolResult` automatically.

#### Memory Tools (`memory_tools.py`)

All memory tools operate strictly within the **current user session** (`ctx.conversation`). They cannot leak data across sessions or access global agent memory directly.

| Tool | Purpose | Key Operations |
|---|---|---|
| `memory_get_history` | Recent interaction history | `conversation.get_interaction_history()` |
| `memory_get_user_info` | Current user profile | `conversation.user` attributes |
| `memory_update_user_model` | Store user fact/preference | `preference.X` keys → preferences dict; other → facts list |
| `memory_set_preference` | Set conversation‑scoped preference | `conversation.set_preference()` |

#### Artifact Tools (`artifact_tools.py`)

Artifacts are arbitrary structured data (results from tools, full documents, file listings, image interpretations, etc.) stored within the **current interaction**. They provide the model with a way to persist intermediate results that can be retrieved later in the same task.

| Tool | Purpose | Key Operations |
|---|---|---|
| `artifact_add` | Store a new artifact | Accepts `key` (unique name), `data` (string or JSON), `tags` (optional list or comma-separated string). Stored on `interaction.artifacts`. Errors if key already exists. |
| `artifact_get` | Retrieve an artifact by key | Returns the raw saved data. |
| `artifact_update` | Overwrite an existing artifact | Replaces data (and optionally tags) for the given key. |
| `artifact_delete` | Remove an artifact | Deletes by key. |
| `artifact_search` | Search artifacts by query and/or tag | Ranks and returns summarised entries; omit both filters to list everything. |

**Storage shape**: `Interaction.artifacts: Dict[str, {"data": str, "tags": List[str], "created_at": iso, "updated_at": iso, "source": str}]`.

**Lifecycle**: artifacts are bound to the interaction and are automatically pruned when the interaction itself is pruned by `Conversation.interaction_limit`. Setting `interaction_limit` to 0 (limitless) means artifacts persist alongside their interactions.

#### Response Tools (`response_tools.py`)

These tools realise the persona’s publishing capabilities. They correspond directly to the two publishing modes that the PersonaAction makes available: a light‑prompt direct send, and a full directive‑based persona‑infused delivery.

| Tool | Purpose | Key Behavior |
|---|---|---|
| `response_publish` | Publish message to user (light prompt) | `finalize=True` sets `cockpit_finalized` flag → loop terminates. Uses `persona.respond_slim()` if available. |
| `response_emit_thought` | Emit reasoning thought | Not shown to user; recorded for audit. |
| `response_deliver_via_persona` | Deliver via full PersonaAction processing | "respond" mode: directive + respond(); "publish" mode: respond_slim(). |

#### Task Tools (`task_tools.py`)

The underlying task service is referred to as the **task manager** (internally still represented by `TaskStore`). These tools give the model the instruments to plan and track multi‑step jobs.

| Tool | Purpose | Key Operations |
|---|---|---|
| `task_create_plan` | Create structured plan with steps | `task_store.create()` → `task.start()` → `task.set_plan(steps)` |
| `task_update_step` | Update step status | Looks up active task; `step.start()` / `step.complete()` / `step.fail()` / `step.skip()` |
| `task_get_status` | View plan progress | Renders status icons for each step |
| `task_add_step` | Add step mid‑execution | `task.add_step(description)` |

**Important**: `TaskStore.list()` is synchronous (`def`, not `async def`). The task tools must NOT `await` it.

#### Unified Search Tool (`search_tools.py`)

`cockpit_search` is the single discovery instrument for finding the most appropriate capability for a job. The same implementation is used in two surfaces with different `permitted_kinds`:

- **Router (Phase 1)**: `permitted_kinds = {skills, interact_actions, tools}`. Optional, gated by `router_use_cockpit_search` (default off — protects routing latency). When enabled, the router enriches its prompt with a capability search section so it can pick a better processing gate / skill set.
- **Engine (Phase 2)**: `permitted_kinds = {skills, tools}`. `interact_actions` is **intentionally hidden** — the cockpit engine has no mechanism to invoke other InteractActions and should not learn about them. The schema enum for the `kind` parameter is dynamically built from `permitted_kinds`, so the model literally cannot ask for a kind that isn't allowed in its context.

| Tool | Purpose | Key Behavior |
|---|---|---|
| `cockpit_search` | Unified capability search | Accepts `query` (intent-focused phrase) and optional `kind` filter (`skills`, `tools`, `interact_actions`, or `all`). Returns ranked results grouped by kind. Uses the same token-overlap weighting as `SkillCatalog.search()` for consistent ranking across kinds. |

The tool is advertised prominently in the engine's system prompt only when the skill catalog is large enough that listing skills inline isn't viable.

#### Conversation Tools (`conversation_tools.py`)

| Tool | Purpose | Key Operations |
|---|---|---|
| `conversation_search` | Search history by keyword | `conversation.get_interaction_history()` + keyword filter |
| `conversation_summarize` | Brief summary of recent exchanges | `conversation.get_interaction_history()` + format |

#### Skill Tools (`skill_tools.py`)

| Tool | Purpose | Key Behavior |
|---|---|---|
| `skill_list` | List all installed skills | `SkillCatalog.render_catalog()` |
| `skill_search` | Search skills by keyword | `SkillCatalog.search(query)` — **synchronous**, do NOT `await` |
| `skill_read` | Read full skill SOP | Returns description, content, and allowed_tools |

### Action Tools

Collected from all enabled actions via `agent.get_actions_manager().get_all_tools()`. Registered with `"action"` prefix. These are whatever tools each action exposes through `Action.get_tools()`.

### Skill Tools

Loaded dynamically from skill bundle directories. Each skill's `discovered_skills[name]` entry contains:

- `dir`: Skill directory path
- `tool_files`: List of `.py` file paths
- `allowed_tools`: Whitelist of tool names (empty = allow all)

For each tool file, `_load_tool_module()`:
1. Uses `importlib.util.spec_from_file_location` to load the module
2. Expects `get_tool_definition()` → dict and `execute(**kwargs)` → str
3. Wraps `execute` in an async wrapper that handles both sync and async returns
4. Qualifies the tool name as `{skill_name}__{raw_tool_name}`
5. Skips tools not in `allowed_tools` whitelist

### Tool Serialization

`ToolSerializer.serialize_all()` converts every `Tool` in the registry to OpenAI‑compatible function‑calling format:

```python
{
  "type": "function",
  "function": {
    "name": "qualified_name",
    "description": "...",
    "parameters": { ... }
  }
}
```

### Tool Execution

`ToolExecutionEngine.dispatch(tool_calls)`:
- Uses `asyncio.Semaphore(max_concurrent)` for parallelism control
- Uses `asyncio.wait_for(timeout)` for per‑tool timeouts
- Sanitizes errors when `sanitize_errors=True` (replaces detailed tracebacks with generic messages)
- Returns `List[ToolResult]`, each with `.content`, `.is_error`, and `.tool_result_message()`

---

## CockpitRouter

### Purpose

Lightweight LLM‑based classifier that determines posture, intent, and skill recommendations before the heavy engine loop runs.

### Routing Flow

1. Collect skill descriptors from `SkillCatalog` (via `CockpitVisitorShim`)
2. Collect interaction history from conversation
3. Build routing prompt from templates:
   - System prompt: explains posture/intent/skills schema
   - User prompt: utterance + skill list + history + canned response instructions
4. Call `model_action.generate()` with the router model
5. Parse JSON response via `parse_routing_response()` → `RoutingResult`
6. Validate recommended skills exist in catalog
7. Return `(posture, RoutingResult)`

### Posture Semantics

| Posture | Meaning | Cockpit Behavior |
|---|---|---|
| `RESPOND` | Handle normally | Continue to gate check → cockpit engine |
| `DEFER` | Delegate to cockpit/tools | Continue to gate check → cockpit engine |
| `SUPPRESS` | Ignore silently | Return immediately, no response |

`DEFER` and `RESPOND` both proceed to the cockpit. Only `SUPPRESS` bails out.

### Canned Responses

If `enable_canned_response=True` and the intent is not in `skip_canned_for_intents` (default: CONVERSATIONAL, UNCLEAR, INTERACTIVE), the router may produce a brief canned response (max 8 words). This is published directly as the interaction response, bypassing the cockpit engine.

### Routing Cache

If `enable_routing_cache=True`, results are cached per `(agent, utterance)` key. Disabled by default.

---

## RoutingResult

```python
@dataclass
class RoutingResult:
    posture: str = "RESPOND"           # RESPOND | SUPPRESS | DEFER
    interpretation: str = ""           # Brief summary of user intent
    intent_type: str = "UNCLEAR"       # CONVERSATIONAL|INFORMATIONAL|DIRECTIVE|INTERACTIVE|UNCLEAR
    actions: List[str] = []            # Recommended skill names
    interact_actions: List[str] = []   # (future: interact‑level actions)
    confidence: float = 0.0            # 0.0-1.0
    verification: Optional[VerificationTrace] = None
    extracted_entities: Dict[str, Any] = {}
    canned_response: str = ""          # Brief canned reply if applicable
    needs_clarification: bool = False   # Confidence below threshold
    raw_response: str = ""             # Raw LLM output for debugging
```

### Parsing (`parse_routing_response`)

- Strips markdown code fences (```json ... ```)
- Handles nested JSON in values
- Enforces the conversational rule: if intent is CONVERSATIONAL, clears recommended actions
- Validates posture and intent_type against allowed values
- Parses confidence as float, clamped to [0.0, 1.0]

---

## Gates

### `should_use_conversational_gate(routing, converse_enabled)`

Returns `True` (use PersonaAction‑only path) when:
- `converse_enabled` is True AND
- Intent is CONVERSATIONAL OR no actions were recommended

Returns `False` (use cockpit engine) when:
- `converse_enabled` is False OR
- Intent is INFORMATIONAL or DIRECTIVE

### `should_enter_processing_gate(routing, converse_enabled)`

Logical inverse of `should_use_conversational_gate`.

---

## Response Delivery

### Conversational Delivery (`deliver_conversational`)

| Mode | Behavior |
|---|---|
| `"respond"` | Add directive to interaction, call `action.respond()` |
| `"publish"` | Build prompt from history, call `persona.respond_slim()` |

Falls back to `action.respond()` if PersonaAction is unavailable.

### Final Response Delivery (`deliver_final_response`)

Delivery matrix (evaluated in priority order):

| Condition | Delivery Method |
|---|---|
| Verbatim override + not degenerate | Publish raw content |
| `"respond"` mode + not degenerate | Add directive, call `action.respond()` |
| Degenerate (<=25 chars) | Publish raw content |
| Default (`"publish"` + not degenerate) | `persona.respond_slim()` |

**Verbatim override**: Any activated skill can declare `verbatim_final: true` in its metadata, forcing the raw engine output to be published without persona rewording.

**Degenerate response**: If the final response is shorter than `degenerate_response_max_chars` (default 25), it's published raw — too short for persona rewording to add value.

**Effective response mode**: Resolved from `SkillCatalog.get_response_mode_override()` if skills are activated, otherwise defaults to `response_mode` config value.

---

## SkillCatalog

### Discovery (`SkillCatalog.discover()`)

Class method with TTL cache (60s, max 200 entries). Cache key is derived from agent identity, skills selector, skills source, denied skills, and app root.

1. Resolve skill bundles from configured sources (`skills_source`: `"builtin"`, `"app"`, or `"both"`)
2. Apply `skills_selector` filter
3. Exclude `denied_skills`
4. Store results in `visitor._skill_state["discovered_skills"]`
5. Create `SkillCatalog` instance, store in `visitor._skill_state["skill_catalog"]`

### Rendering

- `render_catalog()` — Full list for `skill_list` tool
- `render_system_prompt_section()` — Inline skill index for system prompt
- `render_search_mode_system_prompt_section()` — Compact "use skill_search or cockpit_search" prompt for large catalogs

### Search (`search()`)

**Synchronous** method (not async). Token‑overlap scoring with weighted fields:

| Field | Weight |
|---|---|
| Name | 4 |
| Tags | 3 |
| Description | 2 |
| Tools | 1 |
| Requires | 0.5 |
| Substring match bonus | 0.5 |

Returns top‑k results (default 5) as a formatted string.

---

## ActionResolver

Resolves graph‑persisted Actions by entity type for skill tool modules. Created during `_start_cockpit()` and attached to `CockpitVisitorShim.action_resolver`.

| Method | Purpose |
|---|---|
| `resolve(entity_type)` | Return first matching Action (cached) |
| `require(entity_type)` | Like resolve but raises ValueError if absent/disabled |
| `validate_requirements(types)` | Validate all required actions exist and are enabled |
| `validate_action_ref_versions(constraints)` | Validate namespace/label refs against installed versions |

Uses inlined `version_satisfies()` instead of importing from `jvagent.action.skill.version_utils` to maintain self‑containment.

---

## CockpitConfig

Dataclass mirroring all `CockpitInteractAction` attribute fields. Created by `_build_cockpit_config()` which copies every attribute value from the action instance.

### Key Configuration Fields

| Field | Default | Purpose |
|---|---|---|
| `model` | `claude-sonnet-4-20250514` | Main engine LLM |
| `router_model` | `gpt-4o-mini` | Lightweight routing LLM |
| `max_iterations` | `25` | Think‑act‑observe loop cap |
| `max_duration_seconds` | `300.0` | Time budget |
| `max_concurrent_tools` | `5` | Parallel tool call cap |
| `tool_call_timeout` | `60.0` | Per‑tool timeout |
| `sanitize_tool_errors` | `True` | Sanitize detailed error tracebacks |
| `plan_first` | `True` | Inject task planning instructions |
| `enable_artifact_tools` | `True` | Expose artifact CRUD tools |
| `enable_cockpit_search` | `True` | Expose unified search tool to the engine (skills + tools) |
| `router_use_cockpit_search` | `False` | Run cockpit_search in the router (skills + interact_actions + tools) — opt-in, latency-sensitive |
| `stream_internal_progress` | `True` | Single switch for streaming model thoughts, reasoning, and tool progress |
| `stuck_detection_window` | `4` | Sliding window for stuck check |
| `stuck_intent_jaccard_threshold` | `0.65` | Jaccard threshold for stuck detection |
| `stuck_min_iterations` | `4` | Minimum iterations before stuck detection engages (avoids false positives during early multi-step work) |
| `stuck_primary_tool_repeat` | `4` | Consecutive same‑tool threshold |
| `response_mode` | `"publish"` | Delivery mode (publish/respond) |
| `skills_source` | `"both"` | Skill bundle source (builtin/app/both) |
| `enable_skill_helper_tools` | `True` | Expose skill_list/search/read |
| `skill_index_inline_max_skills` | `5` | Max skills inlined in prompt |
| `history_limit` | `5` | Conversation history depth |
| `degenerate_response_max_chars` | `25` | Short‑response threshold |

---

## CockpitContext

Dataclass carrying all runtime references needed by the engine and tool factories.

| Field | Type | Source |
|---|---|---|
| `utterance` | `str` | `visitor.utterance` |
| `conversation` | `Conversation` | `visitor.conversation` |
| `interaction` | `Interaction` | `visitor.interaction` |
| `agent` | `Agent` | `visitor._agent` |
| `model_action` | `LanguageModelAction` | `action.get_model_action()` |
| `config` | `CockpitConfig` | `action._build_cockpit_config()` |
| `response_bus` | `ResponseBus` | `visitor.response_bus` |
| `session_id` | `str` | `visitor.session_id` |
| `channel` | `str` | `visitor.channel` |
| `stream` | `bool` | `visitor.stream` |
| `user_id` | `Optional[str]` | `visitor.user_id` |
| `persona` | `PersonaAction` | `action._require_persona()` |
| `action` | `CockpitInteractAction` | `self` |
| `visitor` | `InteractWalker` | `visitor` |
| `preloaded_skills` | `List[str]` | `routing.actions + always_active` |
| `publish_callback` | `Optional[Callable]` | `action._build_publish_callback()` |

**Properties**: `agent_name` → `persona.persona_name`, `agent_description` → `persona.persona_description`

---

## Integration with jvagent

### Graph Hierarchy

The cockpit plugs into the standard jvagent action pipeline:

```
Root → App → Agent → Actions → CockpitInteractAction
```

An agent's `agent.yaml` declares cockpit as an interact action:

```yaml
actions:
  - type: CockpitInteractAction
    context:
      model: claude-sonnet-4-20250514
      max_iterations: 25
      plan_first: true
      ...
```

### InteractWalker Integration

The `InteractWalker` visits actions in weight order (lowest first). `CockpitInteractAction.weight = -200` gives it high precedence.

**First visit**: `execute()` detects no engine, runs Phase 1 (routing + setup).

**Revisits**: `execute()` detects existing engine (from `_skill_state`), skips routing, runs Phase 2 (next step).

**Walker interaction**:
- `visitor.prepend([self])` — re‑adds cockpit to walk path when model makes tool calls
- `visitor.unrecord_action_execution()` — prevents duplicate recording on revisits
- `visitor._skill_state` — shared dict for state persistence across visits

The walker itself acts as the **cockpit switchboard**, providing every action (including the cockpit) with access to session‑scoped user data, the task manager, the response bus, and other foundational controls — exactly as intended.

### Model Action Resolution

The cockpit uses two model actions:

| Purpose | Resolution | Default |
|---|---|---|
| Skill (engine) | `model_action_type` → `AnthropicLanguageModelAction` → `LanguageModelAction` | `claude-sonnet-4-20250514` |
| Router | `router_model_action_type` or fallback to skill type | `gpt-4o-mini` |

`get_model_action(purpose="skill"|"router")` resolves the appropriate `LanguageModelAction` instance from the agent's actions manager.

### PersonaAction Integration (Duck‑Typed)

The cockpit does NOT import `PersonaAction`. Instead it uses duck‑typing:

- `_require_persona()`: `self.get_action("PersonaAction")`, validates `persona.enabled` and `persona.persona_description`
- Delivery: `hasattr(persona, "respond_slim")` to check for slim delivery support
- Fallback: If persona is unavailable, falls back to raw `action.publish()` or `action.respond()`

### Task Manager (TaskStore) Integration

The underlying task service — referred to as the **task manager** — is accessed via `visitor.tasks` (a `TaskStore` property on the walker). Key APIs used:

| API | Sync/Async | Used by |
|---|---|---|
| `task_store.create(title, description, owner_action)` | async | `task_create_plan` |
| `task.start()` | async | `task_create_plan` |
| `task.set_plan(steps: List[str])` | async | `task_create_plan` |
| `task_store.list(status="active")` | **sync** | `task_update_step`, `task_get_status`, `task_add_step` |
| `task.list_steps()` | **sync** | `task_update_step`, `task_get_status` |
| `step.start()` | async | `task_update_step` |
| `step.complete(result)` | async | `task_update_step` |
| `step.fail(reason)` | async | `task_update_step` |
| `step.skip(reason)` | async | `task_update_step` |
| `task.add_step(description)` | async | `task_add_step` |

**Critical**: `task_store.list()` and `task.list_steps()` are synchronous. Never `await` them.

---

## Observability

### Metrics

Every cockpit run emits observability metrics to `interaction.observability_metrics`:

- Model calls: provider, model, token usage, duration, tool calls, finish reason
- Called by: `"CockpitInteractAction"` (router) or the model action type (engine)

### Tool Progress Streaming

When `config.stream_internal_progress=True`, each tool call result emits a transient thought to the response bus:

```
[ok] tool_name        # success
[failed] tool_name    # error
```

### Engine State Capture

`engine.save_state()` returns a `CockpitState` snapshot for debugging:

```python
@dataclass
class CockpitState:
    messages: List[Dict]          # Full message history
    iteration: int                # Current iteration count
    activated_skills: List[str]   # Skills activated during run
    started_at: float             # monotonic timestamp
    tools_serialized: List[Dict]  # Tool schemas available
    recent_tool_names: List[List[str]]  # Tool name history for stuck detection
```

---

## Common Pitfalls

1. **`await` on sync methods**: `TaskStore.list()`, `Task.list_steps()`, and `SkillCatalog.search()` are synchronous. Never `await` them.

2. **DEFER is not SUPPRESS**: Both postures are distinct. `DEFER` means "delegate to the cockpit" and must proceed to the engine. `SUPPRESS` means "ignore silently."

3. **Dispatch before check**: Tool calls must be dispatched BEFORE checking the `cockpit_finalized` flag. Side effects from other tools in the batch must execute even when `response_publish(finalize=true)` is in the same batch.

4. **Engine instance persistence**: The engine is stored as a live object reference in `_skill_state`, not serialized/deserialized. Never create a new engine instance on revisit.

5. **Interaction‑ID guard**: Always check `cockpit_interaction_id` on entry. If it doesn't match the current interaction, clear all cockpit state and re‑route.

6. **plan_first is not cosmetic**: When `plan_first=True`, the `TASK_PLANNING_BLOCK` must be injected into the system prompt. Without it, the model doesn't know task tools exist and won't create plans for multi‑step tasks.

7. **PersonaAction duck‑typing**: Never `isinstance(action, PersonaAction)`. Use `hasattr(persona, "respond_slim")` and `self.get_action("PersonaAction")` to avoid circular imports.

8. **Memory and artifact scoping**: All memory tools and artifact tools are bound to the conversation (session). They must not access global agent memory or cross‑session data inadvertently.