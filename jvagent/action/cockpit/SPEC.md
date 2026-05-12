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

It has **zero** imports from `jvagent.action.router` or `jvagent.action.persona`. Utilities that would create circular dependencies (e.g., `version_satisfies`) are inlined locally. PersonaAction is accessed via duck‑typing (`hasattr(persona, "respond_slim")`) and `self.get_action("PersonaAction")`.

---

## Architecture

### Layout

The package is grouped into top-level entry modules and five subpackages:

| Path | Role |
|---|---|
| `__init__.py` | Public API re‑exports |
| `cockpit_interact_action.py` | Main InteractAction entry point |
| `engine.py` | Think‑act‑observe engine (one model call per step) |
| `config.py` | CockpitConfig dataclass |
| `context.py` | CockpitContext, CockpitStepResult, CockpitResult, CockpitState |
| `contracts.py` | TerminationReason enum |
| `routing/router.py` | CockpitRouter (Phase 1 lightweight LLM routing) |
| `routing/types.py` | Posture constants, RoutingResult, parse/format utilities |
| `delivery/helpers.py` | Conversational + final-response delivery helpers |
| `delivery/delegation.py` | Resolve and prepend routed `InteractAction`s on the walk path |
| `delivery/gates.py` | Conversational vs processing gate decisions |
| `registry/assembler.py` | `assemble_cockpit_tools` (harness + action + skill layers) |
| `registry/access.py` | Per-user access filtering for skills / interact actions / tools |
| `registry/shim.py` | CockpitVisitorShim (minimal visitor stand‑in) |
| `catalog/skill_catalog.py` | SkillCatalog (discovery, rendering, search) |
| `catalog/skill_discovery.py` | Always‑active skill detection |
| `catalog/action_resolver.py` | ActionResolver + version constraint helper |
| `tools/skill.py` | skill_list, skill_search, skill_read harness tools |
| `tools/task.py` | task_create_plan, task_update_step, task_get_status, task_add_step |
| `tools/memory.py` | memory_get_history, memory_get_user_info, memory_update_user_model, memory_set_preference |
| `tools/artifact.py` | artifact_search, artifact_add, artifact_get, artifact_update, artifact_delete |
| `tools/search.py` | cockpit_search — unified search across skills, actions, and tools |
| `tools/response.py` | response_publish, response_emit_thought, response_deliver_via_persona |
| `tools/conversation.py` | conversation_search, conversation_summarize |
| `tools/clock.py` | get_current_datetime — current date/time/weekday in app timezone |
| `tools/identity.py` | get_user_name — preferred name (display_name → name → "unknown") |

### Dependency Graph

```
CockpitInteractAction
  ├── CockpitRouter ──► routing.types, SkillCatalog, CockpitVisitorShim
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

#### Memory Tools (`tools/memory.py`)

Memory tools span two layers:

**Legacy stable reads** (kept verbatim for stability):

| Tool | Purpose | Key Operations |
|---|---|---|
| `memory_get_history` | Recent interaction history | `conversation.get_interaction_history()` |
| `memory_get_user_info` | Current user profile | `User` node attributes |
| `memory_set_preference` | Set conversation‑scoped preference | `Conversation.context["preferences"]` |

**Phase B: general-purpose key→markdown memory** with two scopes:
- `user`: cross-session, persists with the `User` node (`User.memory: Dict[str, str]`).
- `conversation`: session-scoped, persists with the `Conversation` node (`Conversation.memory: Dict[str, str]`). Auto-cleaned with the conversation.

Each scope has a parallel `memory_tags: Dict[str, List[str]]` for filtered retrieval. Reads default to `auto` scope, which searches user-first then conversation; user-scope wins on key collision.

| Tool | Purpose | Notes |
|---|---|---|
| `memory_set(key, content, scope, tags?)` | Create/overwrite a markdown entry | Caller picks scope explicitly |
| `memory_get(key, scope='auto')` | Retrieve an entry's full body | Falls back to legacy `user_model` for back-compat |
| `memory_append(key, content, scope, separator?)` | Append text to an existing entry | Useful for journals / evolving notes |
| `memory_search(query?, tag?, scope='auto', limit?)` | Token search across keys/body/tags | Tag filter is exclusive |
| `memory_list(scope='auto')` | Brief preview of all keys | |
| `memory_delete(key, scope)` | Remove an entry | Scope must be explicit |

The legacy `memory_update_user_model` is soft-deprecated: it routes to `memory_set(scope='user')` so existing callers continue to work. The legacy `User.user_model` remains read-through-only for back-compat (no new writes).

**System-prompt pre-load.** When `preload_user_memory=True` (default), the engine renders the user's `memory` dict as a `# What I remember about you` markdown block and injects it into the system prompt (capped at `user_memory_max_chars`, default 4096). The model gets stable user context for free without spending a tool call. Conversation-scope memory is *not* auto-injected — it's small enough to fetch on demand via `memory_get`/`memory_search`.

**Auto-write policy.** The cockpit only writes memory when the model explicitly calls `memory_set` / `memory_append`. There is no automatic distillation of facts from utterances; this keeps the contract deliberate and observable.

#### Artifact Tools (`tools/artifact.py`)

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

#### Response Tools (`tools/response.py`)

These tools realise the persona’s publishing capabilities. They correspond directly to the two publishing modes that the PersonaAction makes available: a light‑prompt direct send, and a full directive‑based persona‑infused delivery.

| Tool | Purpose | Key Behavior |
|---|---|---|
| `response_publish` | Publish message to user (light prompt) | `finalize=True` sets `cockpit_finalized` flag → loop terminates. Uses `persona.respond_slim()` if available. |
| `response_emit_thought` | Emit reasoning thought | Not shown to user; recorded for audit. |
| `response_deliver_via_persona` | Deliver via full PersonaAction processing | "respond" mode: directive + respond(); "publish" mode: respond_slim(). |

#### Task Tools (`tools/task.py`)

The underlying task service is referred to as the **task manager** (internally still represented by `TaskStore`). These tools give the model the instruments to plan and track multi‑step jobs.

| Tool | Purpose | Key Operations |
|---|---|---|
| `task_create_plan` | Create structured plan with steps | `task_store.create()` → `task.start()` → `task.set_plan(steps)` |
| `task_update_step` | Update step status | Looks up active task; `step.start()` / `step.complete()` / `step.fail()` / `step.skip()` |
| `task_get_status` | View plan progress | Renders status icons for each step |
| `task_add_step` | Add step mid‑execution | `task.add_step(description)` |

**Important**: `TaskStore.list()` is synchronous (`def`, not `async def`). The task tools must NOT `await` it.

#### Trace Task Design (dual-purpose: model tracking + observability)

Every cockpit run shares ONE trace task between the engine and the model. The engine creates it at `initialize()`, stores its ID on `visitor._skill_state["cockpit_trace_task_id"]`, and finalises it on terminal step result. All four model-facing task tools (`task_create_plan`, `task_update_step`, `task_add_step`, `task_get_status`) resolve to this task — there are no parallel "shadow" tasks.

**Step shape (best practices for dual use):**
- `description` — concise, scannable. For engine-trace steps: `iter N: tool_a(args); tool_b(args); +K more`. For model-authored plan steps: whatever the model named them.
- `result` — short summary on `complete` (`"3/3 ok"`) or `failure_reason` on `fail` (`"all_errors"`).
- `data` (structured bag) — full forensic detail: `{iteration, source, tool_calls: [{tool_call_id, name, arguments, result_preview, result_length, is_error}, …], summary: {ok, errored, total}}`. Tool args are kept full (truncated only in description).
- `data._events` — sub-event log. The engine appends `tool_calls` events here when the model has planned and a step is in-progress, so each model step gathers all the tool calls executed under it.

**Two operational modes:**

1. **Auto mode (model has not called `task_create_plan`).** The engine appends one step per iteration with full tool detail in `data.tool_calls`. The step is `done` if all calls succeeded, `failed` otherwise. Result: a complete chronological execution trace, no model action required.

2. **Plan mode (model has called `task_create_plan`).** The engine preserves all engine-trace steps from before the plan into `task.data.engine_pre_plan_trace` (so observability isn't lost), then `set_plan` installs the model's intentional steps. From this point, the engine no longer appends new steps; instead it attaches each iteration's tool calls as a `tool_calls` sub-event on the model's currently `in_progress` step (via `step.add_event`). The model's plan stays clean (one description per step) while every step accumulates full tool-call detail under `_events`. If no step is in-progress (model planned but hasn't yet marked one in_progress), the engine falls back to appending an `engine_trace` step so observability is never lost.

**Termination:** the engine completes or fails the trace task on every terminal exit path (natural completion, `response_publish(finalize=true)`, time cap, iteration cap, stuck detection, all-errors short-circuit). The result line names the cause (`"completed"`, `"time_cap"`, `"iter_cap"`, `"stuck"`, `"all_errors"`) for forensic clarity.

**Response payload shape — consolidated `tasks` field.** The interact endpoint exposes a single `tasks` array on the interaction payload. Each entry carries its own `status` (`active`, `completed`, `failed`, or `cancelled`) — consumers differentiate by reading the task itself, not by which array it lives in. The consolidation includes:

- All **active** tasks on the conversation.
- Tasks that reached any **terminal** status (completed, failed, cancelled) within this interaction's `started_at` → `completed_at` window.

Tasks are deduplicated by `id` and ordered by `updated_at` ascending, so consumers see chronological progression. Failed and cancelled tasks now surface in the payload — they were invisible to the API consumer before (the legacy filter only matched `status == "completed"`).

#### Unified Search Tool (`tools/search.py`)

`cockpit_search` is the single discovery instrument for finding the most appropriate capability for a job. The same implementation is used in two surfaces with different `permitted_kinds`:

- **Router (Phase 1)**: `permitted_kinds = {skills, interact_actions, tools}`. Optional, gated by `router_use_cockpit_search` (default off — protects routing latency). When enabled, the router enriches its prompt with a capability search section so it can pick a better processing gate / skill set.
- **Engine (Phase 2)**: `permitted_kinds = {skills, tools}`. `interact_actions` is **intentionally hidden** — the cockpit engine has no mechanism to invoke other InteractActions and should not learn about them. The schema enum for the `kind` parameter is dynamically built from `permitted_kinds`, so the model literally cannot ask for a kind that isn't allowed in its context.

| Tool | Purpose | Key Behavior |
|---|---|---|
| `cockpit_search` | Unified capability search | Accepts `query` (intent-focused phrase) and optional `kind` filter (`skills`, `tools`, `interact_actions`, or `all`). Returns ranked results grouped by kind. Uses the same token-overlap weighting as `SkillCatalog.search()` for consistent ranking across kinds. |

The tool is advertised prominently in the engine's system prompt only when the skill catalog is large enough that listing skills inline isn't viable.

#### Conversation Tools (`tools/conversation.py`)

| Tool | Purpose | Key Operations |
|---|---|---|
| `conversation_search` | Search history by keyword | `conversation.get_interaction_history()` + keyword filter |
| `conversation_summarize` | Brief summary of recent exchanges | `conversation.get_interaction_history()` + format |

#### Skill Tools (`tools/skill.py`)

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

### `should_use_conversational_gate(routing, converse_enabled, conversational_fast_path=True)`

Returns `True` (use PersonaAction‑only path — no engine LLM call) when
`converse_enabled` is True and any of these triggers fires:

1. **Skill-driven (preferred).** The router selected the `converse` skill (or
   one of its aliases in `gates.CONVERSE_SKILL_NAMES`) as the only routed
   skill, with no `interact_actions` queued. The dispatch is structural —
   the converse skill exists precisely to mean "no tools, no engine, talk to
   PersonaAction".
2. **Intent fallback.** Router classified the utterance as `CONVERSATIONAL`
   and no `interact_actions` were queued. Kept for back-compat with router
   versions that don't surface `converse` in their skill descriptors.
3. **Empty-route fast-path.** Router recommended no skills, no
   `interact_actions`, and `conversational_fast_path=True` (default). The
   engine has nothing specific to do — saves a heavy LLM round-trip on
   greetings, smalltalk, and unclassifiable utterances.

Returns `False` (use cockpit engine) otherwise. Set
`conversational_fast_path=False` to fall through to the engine for
UNCLEAR / INFORMATIONAL utterances that have no specific handler — useful
when the engine's harness tools (memory, artifacts, conversation search)
should still get a chance to act.

### `should_enter_processing_gate(routing, converse_enabled, conversational_fast_path=True)`

Logical inverse of `should_use_conversational_gate`.

---

## The converse skill

`converse` is a built-in, **always-active** skill bundle
(`jvagent/skills/converse/SKILL.md`). It has no tool files and declares
`always-active: true` in its frontmatter. Always-active skills slip past
selector filtering in `apply_skill_selector` so they appear in the catalog
regardless of the operator's `skills:` list (operators can still opt out
via `denied_skills`).

The router treats `converse` like any other skill — it appears in the skill
descriptors passed to the routing LLM. When the router classifies a request
as `CONVERSATIONAL` and the catalog has `converse`, `CockpitRouter` injects
`actions = ["converse"]` into the routing result. The conversational gate
then fires on the structural skill check and dispatches to PersonaAction.

The cockpit's `_start_cockpit` filters `converse` out of the engine's
`preloaded_skills` list — if the engine ever starts (because other skills
or interact_actions were also routed), surfacing `converse` in the inline
SOP would be incoherent context (it's a routing alias, not an engine
workflow). The catalog still contains it for router visibility.

---

## Response Delivery

All persona handoffs route through `delivery/persona_delivery.py` →
`deliver_via_persona`. The historical helpers (`deliver_conversational`,
`deliver_final_response`, and the action's `_finalize_via_persona`) are now
thin shims over this single entrypoint.

### `deliver_via_persona`

Decision matrix (evaluated in order):

| Condition | Delivery Method |
|---|---|
| `force_raw=True` or skill `verbatim_final` | `action.publish(content)` |
| Content present and ≤ `degenerate_response_max_chars` | `action.publish(content)` |
| Effective mode `"respond"` | `visitor.add_directive(...)` (if directive or content) → `action.respond()` |
| Effective mode `"publish"` + content | `persona.respond_slim(prompt=content)` |
| `"publish"` mode + persona unavailable | `action.publish(content)` (fallback) |

**Verbatim override**: Any activated skill can declare `verbatim_final: true` in its metadata, forcing the raw engine output to be published without persona rewording.

**Degenerate response**: If the final response is shorter than `degenerate_response_max_chars` (default 25), it's published raw — too short for persona rewording to add value.

**Effective response mode**: Resolved from `SkillCatalog.get_response_mode_override()` if skills are activated, otherwise defaults to `response_mode` config value.

### Conversational delivery (`deliver_conversational`)

Thin shim over `deliver_via_persona` with `mode="respond"` and a directive
sourced from the cockpit's `converse_persona_prompt` (default: "Reply
briefly in character; match the user's tone."). Used when the conversational
gate triggers — single PersonaAction call, no engine round-trip.

### Final response delivery (`deliver_final_response`)

Thin shim over `deliver_via_persona` with the engine's `CockpitResult`,
applying per-skill `response_mode` and `verbatim_final` overrides via the
supplied `skill_catalog`.

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

Uses an inlined `version_satisfies()` helper to keep the cockpit module self‑contained.

---

## CockpitConfig

`CockpitConfig` is a frozen runtime dataclass populated from
`CockpitInteractAction` attribute values via `_build_cockpit_config()`. Every
operator-tunable below appears on **both** the Action (for `agent.yaml`
configuration) and the dataclass (for engine consumption). The two stay in
lock-step — there are no internal-only fields.

### Operator Configuration Reference

The matrix is grouped by concern. Defaults shown reflect the shipped
`CockpitInteractAction`. Set values in `agent.yaml` under the cockpit
action's `context:` block; see "Action Configuration" in
[docs/COCKPIT.md](../../../docs/COCKPIT.md) for worked examples.

#### Engine model

| Attribute | Default | Purpose |
|---|---|---|
| `model` | `claude-sonnet-4-20250514` | Main engine LLM identifier |
| `model_action_type` | `AnthropicLanguageModelAction` | Class name of the resolved language-model action |
| `model_temperature` | `0.3` | Engine sampling temperature |
| `model_max_tokens` | `8192` | Engine response cap |

#### Router model

| Attribute | Default | Purpose |
|---|---|---|
| `router_model` | `gpt-4o-mini` | Lightweight routing LLM |
| `router_model_action_type` | `""` (auto-resolve) | Class name of the routing language-model action; empty falls back to `model_action_type` |
| `router_model_temperature` | `0.1` | Router sampling temperature |
| `router_model_max_tokens` | `400` | Router response cap |

#### Loop bounds + safety

| Attribute | Default | Purpose |
|---|---|---|
| `max_iterations` | `25` | Hard cap on think→act→observe cycles per run |
| `max_duration_seconds` | `300.0` | Wall-clock budget per run |
| `max_concurrent_tools` | `5` | Bounded parallelism for tool dispatch |
| `tool_call_timeout` | `60.0` | Per-tool timeout |
| `sanitize_tool_errors` | `true` | Replace detailed tool errors with a generic message; raw exception still recorded on the envelope |

#### Stuck detection (anti-loop)

| Attribute | Default | Purpose |
|---|---|---|
| `stuck_detection_window` | `4` | Sliding window of recent tool calls examined |
| `stuck_intent_jaccard_threshold` | `0.65` | Jaccard threshold for recent-utterance similarity |
| `stuck_primary_tool_repeat` | `4` | Consecutive identical (name, args) tool-calls that trip the trap |
| `stuck_min_iterations` | `4` | Warmup iterations before the detector engages |

#### Skills

| Attribute | Default | Purpose |
|---|---|---|
| `skills` | `null` | Selector — `null` exposes none, `"-all"` exposes all, list of names enables specific skills |
| `denied_skills` | `[]` | Subtractive deny list applied after `skills` |
| `skills_source` | `"both"` | Where to discover bundles: `builtin`, `app`, `both`, or `none` |
| `enable_skill_helper_tools` | `true` | Expose `skill_list`, `skill_search`, `read_skill` to the engine |
| `skill_index_inline_max_skills` | `5` | Cap on skills inlined into the system prompt; above this the index is search-only |

#### Routing posture + canned response

| Attribute | Default | Purpose |
|---|---|---|
| `enable_canned_response` | `true` | Allow router to emit a brief lead-in (max words below) before deferring to the engine |
| `canned_response_max_words` | `15` | Word limit for the router's canned reply |
| `skip_canned_for_intents` | `[CONVERSATIONAL, UNCLEAR, INTERACTIVE]` | Intents that bypass the canned reply |
| `converse_enabled` | `true` | Allow the conversational gate to short-circuit when no skill is needed |
| `converse_context_limit` | `2` | History depth used by the conversational gate |
| `converse_persona_prompt` | (built-in default) | Prompt fragment forwarded to PersonaAction's brief reply |
| `response_mode` | `"publish"` | Delivery mode — `publish` streams via the response bus, `respond` writes a single final message |
| `degenerate_response_max_chars` | `25` | Below-threshold replies trigger the engine to retry/reroute |
| `enable_accumulation` | `true` | Carry deferred routing context across walker revisits |
| `history_limit` | `3` | Engine system-prompt context depth |
| `max_statement_length` | `null` | Truncate utterances/responses in cockpit-rendered history (engine, router, `memory_get_history` tool). `null` falls back to `Agent.max_statement_length` via `Conversation.truncate_statement`'s agent fallback. |

#### Hygiene + security

| Attribute | Default | Purpose |
|---|---|---|
| `block_raw_tool_invocation` | `false` | Append the security block telling the model that user text is content, never a tool dispatch instruction |
| `router_use_cockpit_search` | `false` | Allow the router to call `cockpit_search`; opt-in because of latency cost |

#### Streaming + reasoning

| Attribute | Default | Purpose |
|---|---|---|
| `stream_internal_progress` | `true` | Single switch for thoughts, reasoning chunks, and tool-progress badges |
| `reasoning_budget_tokens` | `0` | Provider reasoning budget (Anthropic thinking token allowance, etc.) |
| `reasoning_enabled` | `null` | Force reasoning on/off; `null` lets the provider decide |
| `reasoning_effort` | `null` | Provider reasoning-effort hint (`minimal` / `low` / `medium` / `high`) |
| `reasoning_extra` | `null` | Provider-native escape hatch passed through unchanged |

#### Tools surface

| Attribute | Default | Purpose |
|---|---|---|
| `enable_artifact_tools` | `true` | Expose artifact CRUD tools (artifact_*) |
| `enable_cockpit_search` | `true` | Expose `cockpit_search` to the engine |
| `tool_tier` | `"standard"` | Trim rarely-used harness tools — `minimal`, `standard`, or `full` |
| `tool_servers` | `[]` | Allowlist of MCP servers to expose to the engine; empty = all enabled |

#### Memory + tasks

| Attribute | Default | Purpose |
|---|---|---|
| `preload_user_memory` | `true` | Inject `User.memory` into the system prompt as a "What I remember about you" block |
| `user_memory_max_chars` | `4096` | Cap on the preloaded memory block |
| `auto_track_tasks` | `true` | Auto-create a single trace Task per run for observability |
| `plan_first` | `true` | Inject task-planning instructions encouraging `task_create_plan` for multi-step requests |
| `max_task_plan_steps` | `50` | Upper bound enforced on plans created by the engine |

#### Action identity (jvspatial-level)

| Attribute | Default | Purpose |
|---|---|---|
| `weight` | `-200` | Execution weight — runs first among InteractActions |
| `description` | (cockpit blurb) | Human-readable description shown in introspection

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

When `config.stream_internal_progress=True`, the engine emits THREE
flavours of thought messages around every tool call. All are
``category=thought`` and ride the response_bus alongside the model's
final reply, so streaming consumers (UIs, audit logs, observability
sinks) see them inline in the SSE stream.

#### 1. `thought_type=tool_call` (pre-execution, structured)

Published by ``CockpitEngine._emit_tool_call`` BEFORE
``ToolExecutor.dispatch`` runs. One per planned tool call. Lets
consumers render "calling X with Y" the moment the model decides,
without waiting for execution.

```
{
  "category": "thought",
  "thought_type": "tool_call",
  "content": "calling <tool_name>",       # human-readable line
  "segment_id": "<openai tool_call_id>",  # pairs with tool_result
  "metadata": {
    "tool_call_id": "<openai tool_call_id>",
    "tool_name":    "<dotted skill / tool name>",
    "tool_args":    { ... parsed kwargs dict ... },
    "iteration":    <int>,
  },
}
```

#### 2. `thought_type=tool_result` (post-execution, structured)

Published by ``CockpitEngine._emit_tool_result`` AFTER dispatch
returns. One per completed call. The ``segment_id`` matches the
prior ``tool_call`` envelope's ``segment_id`` so consumers can
stitch the call/result pair together.

```
{
  "category": "thought",
  "thought_type": "tool_result",
  "content": "ok: <tool_name>" | "error: <tool_name>",
  "segment_id": "<same as the matching tool_call>",
  "metadata": {
    "tool_call_id": "<same as the matching tool_call>",
    "tool_name":    "<dotted skill / tool name>",
    "tool_result":  <the actual tool return value — JSON-serializable>,
    "is_error":     <bool>,
    "iteration":    <int>,
  },
}
```

#### 3. `thought_type=tool_progress` (post-execution, summary)

Published by ``CockpitEngine._emit_tool_progress``. Cheap one-line
summary kept for back-compat with log scrapers and any consumer
that doesn't want the full structured payload:

```
[ok] tool_name        # success
[failed] tool_name    # error
```

All three are emitted on every tool call when
``stream_internal_progress=True`` — they're additive, not
mutually exclusive. Consumers can subscribe to any subset.

The Integral AI-chat SPEC §7.3 structured-envelope requirement is
satisfied by (1) and (2). Older consumers keying off (3) are
unaffected.

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