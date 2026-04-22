# SkillInteractAction

`SkillInteractAction` runs a long-lived think-act-observe loop for multi-step tasks with tool use, progressive skill loading, and per-conversation task tracking.

## Overview

When activated by the InteractRouter, this action:

1. Resolves **skill bundles** from built-in and app-local catalogs via `SkillCatalog`
2. Initializes a **ToolExecutor** with tools from configured MCP servers
3. Runs the agentic loop: LLM thinks, calls tools, observes results, and iterates
4. Tracks multi-step progress via shared **TaskService** on `Conversation.active_tasks`
5. Streams intermediate progress (thinking, tool calls, tool results) as thought-track messages via `publish_thought()`
6. Publishes the final response when the loop completes

### Key Capabilities

- **Progressive skill disclosure**: Skills are exposed via a `read_skill` tool. Their Python tool modules are only loaded when the LLM calls `read_skill`, keeping the tool list lean until specialization is needed.
- **Tool name namespacing**: Skill bundle tools are registered with a `{skill_name}__` prefix (e.g., `calendar__list_events`) to prevent silent handler overwrites when multiple bundles share tool filenames.
- **Deterministic tool dispatch**: The LLM decides which tool to call; ToolExecutor dispatches directly via `MCPClientWrapper.call_tool()`, bypassing `MCPAction.fulfill()` NL-to-tool mapping.
- **Multi-provider support**: Provider-specific shaping is handled in each `LanguageModelAction` via `translate_reasoning_config()` and `prepare_messages_for_reasoning()`.
- **Extended thinking/reasoning**: Generic reasoning config on `SkillInteractAction` is translated per provider and streamed as `thought_type="reasoning"`.
- **Grounding by default**: Prompt policy, per-skill activation reminders, and forced-termination constraints discourage hallucinations and unsupported claims.
- **Skill helper tools**: Optional `list_skills` and `skill_search` tools improve skill selection when many bundles are available.
- **Metadata-driven skill search**: `SkillCatalog.search()` uses weighted token overlap over structured skill metadata (name, tags, description, scope_hint) — language-agnostic, no hardcoded synonyms or domain biases.
- **Stuck-loop recovery**: `StuckDetector` tracks tool-call signatures in a sliding window, injects mid-course correction prompts, and forces graceful termination when needed.
- **Context window management**: `LoopContext.maybe_truncate()` automatically summarizes old tool results, handling both OpenAI and Anthropic message formats.
- **Forced termination**: Graceful summarization when iteration or duration limits are reached.

## Architecture

```
jvagent/action/skill/
  __init__.py                      # Package exports (SkillInteractAction, ToolExecutor, SkillCatalog, LoopContext, StuckDetector)
  skill_interact_action.py         # SkillInteractAction (InteractAction) + LoopState, TerminationReason (~810 lines)
  skill_catalog.py                 # SkillCatalog: discovery, rendering, search, activation validation, response mode
  loop_context.py                  # LoopContext + LoopContextConfig: message building, truncation, format conversion
  stuck_detector.py                 # StuckDetector + StuckDetectorConfig: stuck-loop detection
  tool_executor.py                 # ToolExecutor: tool dispatch, skill activation, namespacing, security
  action_resolver.py               # ActionResolver: resolves graph-persisted Actions for skill tools
  tool_registry.py                  # ToolRegistry + ToolHandle (internal to ToolExecutor)
  prompts.py                       # System prompt templates
  info.yaml                        # Package: jvagent/skill_interact_action

jvagent/scaffold/
  skill_resolve.py                 # Skill bundle resolution and filtering

jvagent/skills/                    # Built-in skill catalog
  calendar/       SKILL.md + list_events.py + create_event.py + delete_event.py
  gmail/          SKILL.md + send_email.py + list_messages.py + get_message.py + mark_read.py + get_profile.py
  google_sheets/  SKILL.md + 14 tool .py files
  google_drive/   SKILL.md + 6 tool .py files
  outlook_calendar/ SKILL.md + list_events.py + create_event.py + delete_event.py
  outlook_mail/   SKILL.md + 6 tool .py files
  microsoft_excel/ SKILL.md + 10 tool .py files
  microsoft_onedrive/ SKILL.md + 4 tool .py files
  web_search/     SKILL.md + search.py
  pageindex_search/ SKILL.md + search.py
  pageindex_docs/  SKILL.md + list_documents.py + assimilate.py + delete_document.py
  answer/         SKILL.md + search.py
  code_review/    SKILL.md
  research/       SKILL.md
  triage/         SKILL.md + prioritize_findings.py
```

### Component Call Graph

```
InteractRouter
  |
  v (activates by weight/classification)
SkillInteractAction.execute(visitor)
  |
  +--->SkillCatalog.discover(visitor, skills, skills_source, denied_skills)
  |       Resolves bundles via jvagent.scaffold.skill_resolve
  |       Applies skills/denied_skills/skills_source filters
  |
  +--->visitor.action_resolver = ActionResolver(agent)
  |       Enables skill tools to resolve graph-persisted Actions
  |
  +--->ToolExecutor.initialize(visitor, tool_servers, local_tools_paths)
  |       |
  |       +--->_register_mcp_server() per configured server
  |       +--->_discover_local_tools() per tools dir
  |       +--->_apply_pattern_filters()
  |
  +--->ToolExecutor.register_skill_bundle() per discovered bundle
  |       Stores metadata; tools NOT yet exposed to LLM
  |
  +--->register_dynamic_tool("read_skill")
  |       Handler: activate_skill() + return SOP content
  |
  +--->visitor.tasks.track(description, task_type="AGENTIC_LOOP")
  |
  +--->_run_agentic_loop()
          |
          +--->_build_reasoning_model_config()
          +--->LoopContext.build_initial_messages(system_prompt, utterance, conversation, interaction, skill_index_section)
          |       Injects skill index section from SkillCatalog.render_system_prompt_section()
          +---> LOOP (iteration < max, elapsed < timeout):
          |       |
          |       +--->tools = tool_executor.get_tools_list()  # re-fetched each iteration
          |       +--->_call_model(messages, tools, visitor, kwargs)
          |       |       calls model_action.query_messages()
          |       |       provider applies prepare_messages_for_reasoning() / translate_reasoning_config()
          |       +--->IF thinking_content: publish_thought(thought_type="reasoning")
          |       +--->IF no tool_calls: loop_state=TERMINATE -> BREAK
          |       +--->IF tool_calls:
          |       |       loop_state=TOOLS
          |       |       publish(category="user") any mid-loop assistant text
          |       |         (so it is rendered to the user AND appended to
          |       |          interaction.response for conversation history)
          |       |       publish_thought(thought_type="tool_call") per call
          |       |       LoopContext.build_assistant_content() -> append
          |       |       tool_executor.dispatch() -> result messages
          |       |       publish_thought(thought_type="tool_result") per result
          |       |       Append results
          |       |       StuckDetector.record() -> inject correction or FORCE_TERMINATE
          |       |       LoopContext.maybe_truncate() -> summarize old results
          |       |
          |       +--->IF at limit: _force_termination(), set termination_reason
          |
          +--->return (final_response, termination_reason)
          +--->SkillCatalog.get_response_mode_override() -> resolve "respond" vs "publish"
          +--->IF response_mode == "respond":
          |       visitor.add_directive(final_response)
          |       self.respond(visitor)
          |   ELSE:
          |       publish(visitor, final_response, streaming_complete=True)
          +--->task.complete(status=termination_reason, summary)
          +--->tool_executor.cleanup()
```

## Skill Sources

Skill bundles are resolved from two sources:

1. **Built-in reusable catalog** shipped with jvagent (`jvagent/skills/*`)
2. **App-local custom skills** under `agents/<namespace>/<agent_id>/skills/*/SKILL.md`

Resolution precedence is deterministic: **app-local overrides built-in** when the same skill name exists in both places.

### Per-Agent Skill Controls

`SkillInteractAction` supports explicit skill exposure controls in `agent.yaml`:

```yaml
- action: jvagent/skill_interact_action
  context:
    enabled: true
    skills: -all               # "-all" or list of names/globs, e.g. ["research", "code_*"]
    denied_skills: ["triage"]  # optional subtractive filter (supports globs)
    skills_source: both        # builtin | app | both | none
```

Default behavior is explicit opt-in: if `skills` is omitted (or empty), no SKILL.md bundles are exposed to the loop.

### Skill Bundle Format

```
agents/<namespace>/<agent_id>/
  skills/
    <skill_name>/
      SKILL.md
      <tool>.py   # optional
```

`SKILL.md` frontmatter keys:

- `name` (recommended)
- `description` (recommended)
- `allowed-tools` (optional) -- whitelist of Python tool names to activate from this bundle
- `requires-actions` (optional) -- Action entity types this skill depends on
- `response-mode` (optional) -- override the action's response_mode for this skill
- `version`, `license`, `tags` (optional metadata)

### Progressive Disclosure Flow

1. At action start, bundles are resolved from the configured source (`builtin`, `app`, or `both`) via `SkillCatalog.discover()`.
2. The resolved set is filtered by `skills` and `denied_skills`.
3. Each bundle is **registered** on the ToolExecutor but its Python tools are **not yet exposed** to the LLM.
4. The loop injects a `read_skill` tool and a skill index in the system prompt (and optionally `list_skills` / `skill_search` helper tools).
5. When the model calls `read_skill(skill_name=...)`, the action:
   - **activates** the bundle's Python tool modules via `tool_executor.activate_skill()`
   - tools are registered with a `{skill_name}__` prefix (e.g., `gmail__send_email`) to prevent collisions
   - returns the full SOP content, a list of newly available tools, and grounding/scope reminders
6. Tool definitions are re-fetched each iteration, so newly activated skill tools become available on the next turn.

This mirrors the Claude Code skill model: the LLM discovers capabilities on demand rather than being overwhelmed with every tool at once.

### Tool Name Namespacing

When a skill bundle is activated, its tool modules are registered with a namespaced name:

```
{skill_name}__{tool_name}
```

For example, the `calendar` skill's `list_events` tool becomes `calendar__list_events`. This prevents silent handler overwrites when multiple bundles share the same tool filename (e.g., `search.py` exists in both `web_search` and `pageindex_search`).

The `allowed-tools` whitelist in SKILL.md frontmatter accepts both bare names (`search`) and namespaced names (`pageindex_search__search`). The LLM sees and calls tools using the namespaced names.

## Intelligence & Grounding

SkillInteractAction defaults to a stricter reliability posture:

- **Default-on guardrails**: `strict_grounding=True`, `plan_first=True`, and `enable_skill_helper_tools=True`.
- **Skill selection discipline**: Prompt instructions include when to use skills and when *not* to use them; `list_skills` / `skill_search` are available for disambiguation.
- **Metadata-driven search**: `SkillCatalog.search()` uses weighted token overlap over structured skill metadata — language-agnostic, no hardcoded synonyms or domain biases.
- **Skill-first retry**: When skills are available but no skill bundle has been activated via `read_skill`, the utterance clears a lexical relevance threshold, and there has been **no** tool use yet in the loop, the model may be nudged with `SKILL_FIRST_RETRY_PROMPT` (up to `skill_first_retry_limit` times, default 1). Nudges are **not** sent after any tool call (e.g. `list_skills` already provided evidence), when the utterance matches **meta intent** (what are your skills, who are you, … — see `meta_intent_skip_nudge` / `meta_intent_patterns`), or when the candidate answer already **names a discovered skill** at sufficient length. Optional `conversational_skip_patterns` and a short-utterance + low-relevance heuristic also skip the nudge.
- **Best-candidate recovery**: If a nudge (or a bad second turn) produces a very short or degenerate answer while a better candidate was already produced, the loop prefers the best candidate. With `response_mode: respond`, **PersonaAction** is skipped when the final loop output is still degenerate so the persona cannot invent a substitute answer.
- **Reasoning stream UX**: `mirror_assistant_stream_as_thoughts` can force/disable assistant-stream mirroring into thoughts. When unset, provider defaults apply (OpenAI reasoning models can auto-enable).
- **Task metadata** adds `helper_tools_called`, `meta_intent_detected`, `retry_nudges_fired`, `best_candidate_length`, and step types: `skill_first_retry`, `candidate`, `candidate_accepted`, `candidate_discarded`, `final_review_skipped`.
- **Per-skill scope grounding**: `read_skill` responses append `GROUNDING_INSTRUCTION_TEMPLATE` guidance using each bundle's resolved `scope_hint`.
- **Activation guard**: `max_skill_activations` caps speculative skill loading; overflow returns `SKILL_ACTIVATION_LIMIT_MESSAGE`.
- **Stuck detection**: `StuckDetector` tracks per-iteration tool-call signatures in a sliding window; repeated identical signatures trigger `STUCK_DETECTION_PROMPT`. After `max_midcourse_corrections`, the action forces termination.
- **Optional final review**: `final_review=True` runs a no-tools cleanup pass (`FINAL_REVIEW_PROMPT`) before publishing.

Skill bundles also resolve a derived `scope_hint` (from frontmatter `tags` or `description`) via `jvagent.scaffold.skill_resolve.parse_skill_bundle()`.

## The Agentic Loop

The core algorithm in `_run_agentic_loop()`:

```
1. Build model kwargs (base config + thinking config)
2. Compose initial messages via LoopContext.build_initial_messages()
   (system prompt + skill index + history + user utterance)
3. Initialize loop_state=MODEL, termination_reason=COMPLETED
4. LOOP (iteration < max_iterations):
   a. Check duration limit -> if exceeded: loop_state=TERMINATE, forced termination, termination_reason=TIME_CAP, BREAK
   b. Re-fetch tool list (includes newly activated skill tools)
   c. Record "thinking" step on TaskHandle, loop_state=MODEL
   d. Call LLM via model_action.query_messages()
      (provider translates generic reasoning config and reshapes messages as needed)
   e. Publish reasoning thought (if Anthropic extended thinking)
   f. If no tool_calls -> extract text, termination_reason=COMPLETED, loop_state=TERMINATE, BREAK
   g. If tool_calls present:
      i.    Record "tool_call" step, loop_state=TOOLS
      ii.   If commit_intermediate_messages and the model emitted text
            alongside the tool calls, publish that text as
            category="user" so it is rendered to the user AND appended
            to interaction.response (keeps conversation history complete)
      iii.  Publish tool_call thought (thought_type="tool_call")
      iv.   LoopContext.build_assistant_content() -> append
      v.    Dispatch tool calls via ToolExecutor
      vi.   Publish tool_result thought (thought_type="tool_result", truncated to tool_result_truncation_chars)
      vii.  Append tool result messages
      viii. StuckDetector.record() -> inject correction or FORCE_TERMINATE
      ix.   Record "tool_result" step
      x.    LoopContext.maybe_truncate() -> summarize old results
5. If loop exhausted iterations: forced termination, termination_reason=ITER_CAP
6. If final_review enabled: run no-tools grounding review pass
7. Record "response" step (with loop_state)
8. Return (final_response, termination_reason, stuck_corrections)
```

Returns `tuple[str, str, int]` where the second element is a `TerminationReason` value and the third is stuck-correction count.

## Configuration

All attributes can be overridden via `context` in agent.yaml.

### SkillInteractAction Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `weight` | int | `-60` | Execution weight (after InteractRouter, before Persona) |
| `description` | str | `"Long-running agentic loop for multi-step tasks with tool use."` | Action description |
| `max_iterations` | int | `25` | Hard cap on think-act-observe cycles |
| `max_duration_seconds` | float | `300.0` | Wall-clock timeout (seconds) |
| `reasoning_budget_tokens` | int | `0` | Generic reasoning budget tokens (provider translated) |
| `model_action_type` | str | `"AnthropicLanguageModelAction"` | LanguageModelAction entity type |
| `model` | str | `"claude-sonnet-4-20250514"` | Model identifier |
| `model_temperature` | float | `0.3` | LLM temperature |
| `model_max_tokens` | int | `8192` | Max tokens for LLM generation |
| `skills` | Any | `None` | Skill selector: `"-all"` or list of names/globs or `None` |
| `denied_skills` | List[str] | `[]` | Subtractive filter on resolved bundles (supports globs) |
| `skills_source` | str | `"both"` | Bundle source: `"builtin"`, `"app"`, `"both"`, or `"none"` |
| `response_mode` | str | `"publish"` | How to deliver the final response: `"publish"` (direct bus delivery) or `"respond"` (route through PersonaAction for persona-enriched responses with parameters, directives, and persona attributes) |
| `tool_servers` | List[str] | `[]` | Names of MCPAction instances providing tools |
| `allow_local_tools` | bool | `False` | Whether ToolExecutor can register local Python tools |
| `prioritize_skills_first` | bool | `True` | When skills are available but none activated, nudge only if the utterance is skill-relevant and not skipped by conversational rules |
| `skill_first_retry_limit` | int | `1` | Maximum skill-first retry nudges per loop (`0` disables) |
| `skill_first_retry_min_relevance` | float | `0.25` | Minimum `SkillCatalog.top_relevance_score` for the utterance before nudging |
| `conversational_skip_patterns` | List[str] | `[]` | Optional `re.UNICODE` regexes; if any matches the utterance, skip the nudge (add per-locale phrases yourself) |
| `skill_first_conversational_heuristic` | bool | `True` | Language-agnostic skip when the utterance is short (chars/tokens) and relevance is below `conversational_heuristic_max_relevance` |
| `conversational_short_utterance_max_chars` | int | `60` | Heuristic: max characters |
| `conversational_short_utterance_max_tokens` | int | `8` | Heuristic: max tokens (`SkillCatalog` tokenization) |
| `conversational_heuristic_max_relevance` | float | `3.0` | Heuristic: skip only if `top_relevance_score` is below this |
| `conversational_min_response_chars` | int | `20` | Minimum model reply length before conversational skip applies |
| `meta_intent_skip_nudge` | bool | `True` | If True, skip skill-first nudge for meta/identity questions (see `SkillCatalog.is_meta_intent` + `meta_intent_patterns`) |
| `meta_intent_patterns` | List[str] | `[]` | Extra regexes (merged with catalog defaults) for meta intent detection |
| `degenerate_response_max_chars` | int | `25` | Shorter than this, the reply may be treated as a bare ack when detecting degenerate final output |
| `best_candidate_shrink_ratio` | float | `0.4` | Prefer `best_candidate` if a later answer is shorter than this fraction of the best |
| `reasoning_enabled` | bool \| `None` | `None` | Generic reasoning enable flag; provider maps to native switches |
| `reasoning_extra` | dict \| `None` | `None` | Provider-native reasoning override object (escape hatch) |
| `mirror_assistant_stream_as_thoughts` | bool \| `None` | `None` | Provider-agnostic assistant-stream mirroring toggle for thought stream |
| `stream_thinking` | bool | `True` | Publish reasoning thoughts via `publish_thought()` |
| `stream_tool_progress` | bool | `True` | Publish tool_call and tool_result thoughts via `publish_thought()` |
| `commit_intermediate_messages` | bool | `True` | Publish (and persist) any text the model emits alongside tool calls as a `category="user"` message so the conversation history matches what the assistant said. |
| `relay_thoughts_to_channels` | bool | `False` | Relay thought messages to channel adapters only when adapters opt in (`deliver_thoughts=True`) |
| `max_full_tool_results` | int | `10` | Keep last N tool results in full; summarize older |
| `max_tool_result_tokens` | int | `400` | Max estimated tokens retained for an individual tool result message (uses `estimate_tokens()`) |
| `tool_result_truncation_chars` | int | `500` | Max characters streamed for individual tool-result thought updates |
| `history_limit` | int | `5` | How many prior interactions to include in initial context |
| `call_timeout_seconds` | float | `60.0` | Timeout in seconds for each tool call (passed to ToolExecutor) |
| `task_sync_every_steps` | int | `3` | How many tracker steps to buffer before persisting metadata |
| `local_tools_path` | str | `None` | Absolute path to a directory of local .py tool modules |
| `strict_grounding` | bool | `True` | Enable grounding-focused prompt constraints and per-skill scope reminders |
| `plan_first` | bool | `True` | Instruct model to emit a brief plan before non-trivial tool use |
| `enable_skill_helper_tools` | bool | `True` | Register `list_skills` and `skill_search` dynamic tools |
| `max_skill_activations` | int | `5` | Maximum number of skill activations in one loop |
| `stuck_detection_window` | int | `3` | Consecutive identical tool-call signatures needed to trigger stuck detection |
| `max_midcourse_corrections` | int | `2` | Maximum injected stuck-detection corrections before forced termination |
| `final_review` | bool | `False` | Run optional no-tools final grounding review pass before publish/respond |

### LoopContextConfig Attributes

Configures message lifecycle management in `LoopContext`.

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_full_tool_results` | int | `10` | Keep last N tool results in full; summarize older |
| `max_tool_result_tokens` | int | `400` | Max estimated tokens retained for an individual tool result |
| `tool_result_truncation_chars` | int | `500` | Max characters for individually truncated tool results |
| `history_limit` | int | `5` | How many prior interactions to include |

### StuckDetectorConfig Attributes

Configures stuck-loop detection behavior in `StuckDetector`.

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `window_size` | int | `3` | Consecutive identical signatures to trigger detection |
| `max_corrections` | int | `2` | Maximum corrections before forced termination |

### ToolExecutor Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `call_timeout` | float | `60.0` | Timeout in seconds for each individual tool call |
| `validate_calls` | bool | `True` | Validate tool calls against schema before dispatch |
| `max_concurrent_calls` | int | `5` | Maximum concurrent tool executions (semaphore) |
| `sanitize_errors` | bool | `True` | Replace internal error details with generic messages |
| `allowed_tool_paths` | Optional[List[str]] | `None` | Additional base directories allowed for dynamic tool loading |

## Agent Wiring (agent.yaml)

A complete thinking agent requires: a LanguageModelAction, one or more MCP servers (optional but typical), and the SkillInteractAction itself. Skill bundles are resolved from the built-in catalog and/or app-local `skills/` directories.

### Full Example

```yaml
agent: jvagent/skills_agent
version: 1.0.0

actions:
  # ── Routing ────────────────────────────────────
  - action: jvagent/interact_router
    context:
      enabled: true
      model: "gpt-4.1-mini"

  # ── Model Provider ─────────────────────────────
  - action: jvagent/openai_lm
    context:
      enabled: true
      api_key: ${OPENAI_API_KEY}
      model: "gpt-4.1"
      temperature: 0.2
      max_tokens: 16384

  # ── MCP Servers (Tool Providers) ───────────────
  - action: jvagent/mcp
    context:
      enabled: true
      model_action_type: "OpenAILanguageModelAction"
      model: "gpt-4o-mini"
      servers:
        - name: "filesystem"
          transport: "stdio"
          command: "npx"
          args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
          mcp_connect_timeout: 15
          mcp_call_timeout: 60
          tools: "-all"
          denied_tools: []

  # ── Thinking Agent (Core Loop) ─────────────────
  - action: jvagent/skill_interact_action
    context:
      enabled: true
      weight: -60
      anchors:
        - "User asks to review, analyze, audit, or fix code"
        - "User requests a multi-step task requiring tool use"
      model_action_type: "OpenAILanguageModelAction"
      model: "gpt-4.1"
      model_temperature: 0.2
      model_max_tokens: 16384
      max_iterations: 25
      max_duration_seconds: 300
      reasoning_budget_tokens: 0       # generic budget field (provider translated)
      skills:                          # Skill bundle selector
        - "code_review"
        - "local_research"
      skills_source: both              # builtin | app | both | none
      denied_skills: []                # optional subtractive filter
      tool_servers: ["filesystem"]
      stream_tool_progress: true
      max_full_tool_results: 10

  # ── Fallback ───────────────────────────────────
  - action: jvagent/converse_interact_action
    context:
      enabled: true
      weight: 100
```

### Skill Selector Options

```yaml
# Expose all discovered bundles
skills: -all

# Expose specific bundles by name or glob pattern
skills:
  - "code_review"
  - "triage"
  - "code_*"          # glob pattern matches code_review, code_gen, etc.

# Deny specific bundles (subtracts from resolved set)
skills: -all
denied_skills:
  - "triage"

# Source control
skills_source: builtin   # only jvagent/skills/*
skills_source: app       # only agents/<ns>/<id>/skills/*
skills_source: both       # both (default); app-local overrides built-in on collision
skills_source: none       # disable skill bundle resolution entirely

# Explicit opt-in (default): no bundles if skills is omitted or empty
# skills:        # omitted -> no skill bundles
```

### With Anthropic Extended Thinking

```yaml
- action: jvagent/anthropic_lm
  context:
    enabled: true
    api_key: ${ANTHROPIC_API_KEY}
    model: "claude-sonnet-4-20250514"
    max_tokens: 16384

- action: jvagent/skill_interact_action
  context:
    enabled: true
    model_action_type: "AnthropicLanguageModelAction"
    model: "claude-sonnet-4-20250514"
    reasoning_budget_tokens: 10000
    stream_thinking: true
```

When `reasoning_budget_tokens > 0`, the Anthropic provider translates this to `thinking: {type: "enabled", budget_tokens: N}` and ensures `max_tokens >= budget_tokens + 1`.

### Minimal (Reasoning-Only, No Tools or Skills)

```yaml
- action: jvagent/skill_interact_action
  context:
    enabled: true
    weight: -60
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1"
    max_iterations: 10
    # No tool_servers, no skills -> reasoning-only mode
```

## MCP Tool Integration

SkillInteractAction uses MCP servers as tool providers. Configure MCP providers under one `jvagent/mcp` action (`context.servers`), then reference each provider by `servers[].name` in `tool_servers`.

### How Tools Are Discovered

1. ToolExecutor finds the MCPAction that hosts the requested server name via `agent.get_actions()` iteration
2. Calls `mcp_action.get_tools_cached(server_name)` to get the filtered tool list (cached per server session)
3. Registers each tool in ToolManager with name, description, and input schema
4. Maps `tool_name -> ("mcp", (mcp_action, server_name))` in the handlers registry

### How Tools Are Dispatched

The LLM decides which tool to call (deterministic dispatch). ToolExecutor bypasses `MCPAction.fulfill()` entirely and calls `MCPClientWrapper.call_tool(name, arguments)` directly.

### Tool Result Normalization

MCP tool results are normalized from the SDK's content format:

```python
# Each content item with type="text" is extracted.
# The MCP Python SDK's CallToolResult uses camelCase field names
# (isError, structuredContent). _normalize_call_result() reads
# isError first and falls back to is_error for adapters/tests,
# defaulting to False when neither is set.
text = "\n".join(text_parts).strip()
if is_error and text:
    raise ToolDispatchError(text)
```

## Local Tools

Local Python tools can be registered in three ways:

### 1. Via `local_tools_path` (Auto-Discovery)

Point `local_tools_path` to a directory containing `.py` files. Each file must export:

```python
# my_tool.py
def get_tool_definition() -> dict:
    return {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "The input"}
            },
            "required": ["input"]
        }
    }

async def execute(arguments: dict) -> str:
    result = do_something(arguments["input"])
    return str(result)
```

ToolExecutor scans all `.py` files, imports `get_tool_definition()` and `execute()`, and registers them. **Security**: files outside the current working directory are rejected unless their parent directory is listed in `allowed_tool_paths`.

### 2. Programmatically via `register_local_tool()`

```python
executor = ToolExecutor()
tool_def = executor.register_local_tool(
    name="my_tool",
    handler=my_async_handler,
    description="Does something useful",
    parameters={"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
)
```

### 3. Skill Bundle Tools (Progressive, Namespaced)

Tools inside a skill bundle directory are hidden until the LLM calls `read_skill`. Upon activation, tools are registered with a `{skill_name}__` prefix. See [Progressive Disclosure Flow](#progressive-disclosure-flow) and [Tool Name Namespacing](#tool-name-namespacing).

## Action-Bound Skill Tools

Skill tool modules (`.py` files) can access graph-persisted Actions by accepting a `visitor` keyword argument in their `execute()` function. The visitor object carries an `action_resolver` attribute set by SkillInteractAction at loop startup.

### How It Works

1. SkillInteractAction creates an `ActionResolver(agent)` and attaches it as `visitor.action_resolver` before the agentic loop starts.
2. Skill tools that declare `async def execute(arguments, *, visitor)` in their signature receive the visitor automatically via `_dispatch_local_tool()`.
3. The tool calls `visitor.action_resolver.resolve("SomeActionType")` to get the Action instance, then calls its methods directly.

### SKILL.md Frontmatter: requires-actions

Add a `requires-actions` key to declare which Actions a skill depends on:

```yaml
---
name: calendar
description: Manage Google Calendar events
requires-actions:
  - GoogleCalendarAction
allowed-tools:
  - list_events
  - create_event
  - delete_event
---
```

When the LLM calls `read_skill` for a skill with `requires-actions`, `SkillCatalog.validate_requirements()` checks that each required action is present and enabled on the agent's graph. If any are missing or disabled, the skill fails to activate with a clear error message.

### ActionResolver API

| Method | Signature | Description |
|--------|-----------|-------------|
| `resolve()` | `async resolve(entity_type: str) -> Optional[Action]` | Return the Action or `None` if absent |
| `require()` | `async require(entity_type: str) -> Action` | Return the Action or raise `ValueError` |
| `validate_requirements()` | `async validate_requirements(types: List[str]) -> List[str]` | Validate all; return list of error messages (empty if valid) |

Actions are cached per-interaction, so repeated `resolve()` calls do not re-query the graph.

### Example: Calendar Skill Tool

```python
# skills/calendar/list_events.py

async def execute(arguments: dict, *, visitor: Any) -> Any:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleCalendarAction")
    if action is None:
        return {"error": "GoogleCalendarAction not found on this agent"}

    return await action.list_events(
        calendar_id=arguments.get("calendar_id", "primary"),
        max_results=arguments.get("max_results", 10),
    )
```

## Built-in Skills

jvagent ships with fifteen built-in skill bundles. Tool names are shown in their namespaced form (`{skill_name}__{tool_name}`):

| Skill | Description | Tools | Requires Actions |
|-------|-------------|-------|-----------------|
| `calendar` | Manage Google Calendar events (list, create, delete) | `calendar__list_events`, `calendar__create_event`, `calendar__delete_event` | `GoogleCalendarAction` |
| `gmail` | Send and manage Gmail messages | `gmail__send_email`, `gmail__list_messages`, `gmail__get_message`, `gmail__mark_read`, `gmail__get_profile` | `GoogleGmailAction` |
| `google_sheets` | Read, write, and manage Google Sheets | `google_sheets__read_spreadsheet`, `google_sheets__last_filled_row`, `google_sheets__update_spreadsheet`, `google_sheets__append_spreadsheet`, `google_sheets__batch_clear`, `google_sheets__format_cells`, `google_sheets__merge_cells`, `google_sheets__unmerge_cells`, `google_sheets__create_spreadsheet`, `google_sheets__create_worksheet`, `google_sheets__update_worksheet`, `google_sheets__delete_worksheet`, `google_sheets__share_spreadsheet`, `google_sheets__delete_spreadsheet` | `GoogleSheetsAction` |
| `google_drive` | Upload, share, and manage Google Drive files | `google_drive__upload_file`, `google_drive__delete_file`, `google_drive__get_file_metadata`, `google_drive__list_files`, `google_drive__share_file`, `google_drive__get_media` | `GoogleDriveAction` |
| `outlook_calendar` | Manage Outlook Calendar events (list, create, delete) | `outlook_calendar__list_events`, `outlook_calendar__create_event`, `outlook_calendar__delete_event` | `MicrosoftOutlookCalendarAction` |
| `outlook_mail` | Send and manage Outlook mail messages | `outlook_mail__send_email`, `outlook_mail__list_messages`, `outlook_mail__list_inbox_messages`, `outlook_mail__get_message`, `outlook_mail__mark_read`, `outlook_mail__get_profile` | `MicrosoftOutlookMailAction` |
| `microsoft_excel` | Read, write, and manage Excel workbooks | `microsoft_excel__read_spreadsheet`, `microsoft_excel__update_spreadsheet`, `microsoft_excel__append_spreadsheet`, `microsoft_excel__batch_clear`, `microsoft_excel__create_spreadsheet`, `microsoft_excel__create_worksheet`, `microsoft_excel__update_worksheet`, `microsoft_excel__delete_worksheet`, `microsoft_excel__share_spreadsheet`, `microsoft_excel__delete_spreadsheet` | `MicrosoftExcelAction` |
| `microsoft_onedrive` | Upload, share, and manage OneDrive files | `microsoft_onedrive__upload_file`, `microsoft_onedrive__delete_file`, `microsoft_onedrive__list_files`, `microsoft_onedrive__share_file` | `MicrosoftOneDriveAction` |
| `answer` | RAG with structured retrieval cascade (PageIndex → web → parametric knowledge), citations, and uncertainty signaling | `answer__search` | `PageIndexAction`, `SerperWebSearchAction` |
| `web_search` | Search the web for current information | `web_search__search` | `SerperWebSearchAction` |
| `pageindex_search` | Search PageIndex documents using vectorless retrieval | `pageindex_search__search` | `PageIndexAction` |
| `pageindex_docs` | List, ingest, and remove PageIndex documents | `pageindex_docs__list_documents`, `pageindex_docs__assimilate`, `pageindex_docs__delete_document` | `PageIndexAction` |
| `code_review` | Review code for correctness, security, and maintainability | (SOP only) | — |
| `research` | Investigate a topic with evidence-first synthesis and citations | (SOP only) | — |
| `triage` | Rapidly triage issues by severity, impact, and next action | `triage__prioritize_findings` | — |

The `triage` skill includes `prioritize_findings.py`, which sorts findings by severity. It is only exposed to the LLM after `read_skill(skill_name="triage")` is called.

## App-Local Skills

Create custom skill bundles under your agent's directory:

```
agents/jvagent/my_agent/
  skills/
    my_custom_skill/
      SKILL.md
      my_tool.py   # optional
```

When `skills_source` is `"app"` or `"both"`, these are resolved alongside built-in bundles. If a local skill has the same name as a built-in, the local one wins.

### SKILL.md Format

```markdown
---
name: my_custom_skill
description: What this skill does and when to use it.
allowed-tools:
  - my_tool        # optional: whitelist of .py tool names
version: 1
tags:
  - custom
  - analysis
---

## Workflow

1. First step of the SOP
2. Second step
3. ...
```

### SKILL.md Frontmatter Keys

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `name` | Recommended | str | Skill identifier. Defaults to directory name if omitted. |
| `description` | Recommended | str | Shown in the skill index that the LLM sees. |
| `allowed-tools` | Optional | list[str] | Whitelist of `.py` tool filenames (without `.py`) to activate from this bundle. If omitted, all `.py` tools in the directory are activated. |
| `requires-actions` | Optional | list[str] or str | Action entity types this skill depends on (e.g. `GoogleCalendarAction`). If any are missing or disabled at activation time, `read_skill` returns an error. |
| `response-mode` | Optional | str | Override the action's `response_mode` for this skill: `"respond"` (route through PersonaAction) or `"publish"` (direct bus delivery). If omitted, inherits the action's `response_mode` attribute. |
| `version` | Optional | int/str | Version number for tracking |
| `license` | Optional | str | License identifier |
| `tags` | Optional | list[str] | Tags for categorization and search |

### Tool Modules in Skill Bundles

Each `.py` file (excluding `__init__.py` and `_`-prefixed files) in the skill directory is a potential tool module. It must export:

```python
def get_tool_definition() -> dict:
    return {
        "name": "my_tool",
        "description": "Description for the LLM",
        "parameters": { ... }  # JSON Schema
    }

async def execute(arguments: dict) -> Any:
    # Tool logic here
    return result
```

If `allowed-tools` is set in the frontmatter, only tools whose names match the whitelist are activated. Upon activation, tools are registered with the `{skill_name}__` prefix.

## Response Mode

By default, `SkillInteractAction` delivers the final response via `publish()` — direct bus delivery. This is fast and simple, but bypasses PersonaAction entirely, so the response does not inherit the agent's persona attributes, parameters, or directive pipeline.

Setting `response_mode: "respond"` routes the final response through PersonaAction instead. The skill action injects the final text as a directive via `visitor.add_directive()`, then calls `self.respond(visitor)` — PersonaAction processes the directive through its full pipeline (persona, parameters, formatting) before publishing.

### Configuration

**Action-level (applies to all skills unless overridden):**

```yaml
- action: jvagent/skill_interact_action
  context:
    response_mode: respond    # "publish" (default) or "respond"
```

**Per-skill override (in SKILL.md frontmatter):**

```yaml
---
name: research
description: Investigate a topic with evidence-first synthesis
response-mode: respond        # Override action default for this skill only
---
```

### Resolution Logic

`SkillCatalog.get_response_mode_override()` resolves the effective mode:

1. If any activated skill has `response-mode: respond` → use `respond`
2. Otherwise → use the action's `response_mode` attribute (default: `publish`)

This is additive: `publish` is the default (no behavior change for existing agents), `respond` is opt-in per-action or per-skill.

### When to Use Each Mode

| Mode | Best For | Trade-off |
|------|----------|-----------|
| `publish` | Fast, unstyled responses; tool-heavy workflows where persona formatting is unnecessary | No persona enrichment, no parameter injection |
| `respond` | User-facing conversations where persona, tone, and formatting matter | Adds one extra PersonaAction cycle per interaction |

## CLI Commands

jvagent provides CLI commands for managing skill bundles:

### `jvagent skill add`

Create a SKILL.md bundle skeleton for an agent:

```bash
jvagent skill add <agent_ref> <skill_name> [--description TEXT] [--force]
```

- `agent_ref`: Agent reference (e.g. `jvagent/skills_agent`)
- `skill_name`: Name for the new skill bundle
- `--description`: Frontmatter description (default: generic placeholder)
- `--force`: Overwrite if SKILL.md already exists

### `jvagent skill list`

List available skill bundles:

```bash
jvagent skill list [--agent <agent_ref>] [--builtin]
```

- `--agent`: Show merged bundles (built-in + app-local) for a specific agent
- `--builtin`: Show only built-in bundles

### `jvagent skill show`

Show one skill bundle's metadata and SOP content:

```bash
jvagent skill show <skill_name> [--agent <agent_ref>] [--builtin]
```

## Extended Thinking (Anthropic)

When `model_action_type` is `AnthropicLanguageModelAction` and `reasoning_budget_tokens > 0`:

- The provider adapter translates reasoning config to `thinking: {type: "enabled", budget_tokens: N}`
- Anthropic's `_build_payload()` omits `temperature` (required by the API when thinking is on)
- `max_tokens` is auto-adjusted to `>= budget_tokens + 1` if needed
- Thinking content is accumulated into `model_result.thinking_content` and `thinking_tokens` after each streamed model call
- During the agentic loop, the model is called with **streaming** enabled; thinking deltas arrive live as `thinking_delta` SSE events and are published as `category="thought"` / `thought_type="reasoning"` chunks (see Streaming below)

**Non-Anthropic reasoning:** set generic fields on `SkillInteractAction` (`reasoning_effort`, `reasoning_enabled`, `reasoning_extra`). Each provider maps them to native API parameters.

## Streaming

SkillInteractAction publishes two logical tracks:

- **User track** (`category="user"`): end-user-facing assistant utterances. Both the final response and any mid-loop commentary the model emits alongside tool calls (when `commit_intermediate_messages=True`, the default) are published on this track and appended to `interaction.response` so the persisted conversation history matches what the user saw.
- **Thought track** (`category="thought"`): reasoning/tool telemetry for observability and specialized client rendering.

Thought track events include:

- `thought_type="reasoning"` with `segment_id="iter-{n}-reasoning"`: **streamed** during each LLM call (`stream=True` on `query_messages`), with multiple chunks per iteration (`streaming_complete=False`) and a final empty chunk with `streaming_complete=True` and `allow_empty=True` to close the segment when at least one delta was emitted
- `thought_type="tool_call"` with `segment_id="iter-{n}-call-{tool_name}-{idx}"`
- `thought_type="tool_result"` with `segment_id="iter-{n}-result-{tool_call_id}"`

Forced termination and final-review model calls use **non-streaming** completions and do not emit live reasoning chunks.

Thought messages are never appended to `interaction.response`; they are stored in `interaction.agent_trace`. Clients can route thoughts to a separate panel by checking `category`, `thought_type`, and `segment_id`.

## Task Tracking

Thinking actions use the shared `TaskService` (`visitor.tasks`). Each run creates one `AGENTIC_LOOP` task scoped to the current conversation and guarantees terminal status through `async with visitor.tasks.track(...)`.

### Step Types

| Step Type | When Recorded | Details |
|-----------|--------------|---------|
| `thinking` | Each LLM call | Optional `tokens` count |
| `tool_call` | Tool calls present | `count` of tool calls |
| `tool_result` | After dispatch | `duration_ms` |
| `response` | Final response | `length` |
| `error` | On failure | `error` description |

### Task Metadata Structure

```json
{
  "skills": ["code_review"],
  "skills_source": "both",
  "iterations": 7,
  "tools_called": ["read_file", "search_files"],
  "thinking_tokens_used": 8432,
  "steps": [
    {"type": "thinking", "iteration": 1, "timestamp": "..."},
    {"type": "tool_call", "iteration": 1, "count": 1, "timestamp": "..."},
    {"type": "tool_result", "iteration": 1, "duration_ms": 230, "timestamp": "..."}
  ],
  "started_at": "2026-04-18T10:00:00+00:00",
  "completed_at": "2026-04-18T10:00:45+00:00",
  "total_duration_seconds": 45.2,
  "final_summary": "The code review found 3 issues..."
}
```

### TaskService API (Thinking usage)

```python
async with visitor.tasks.track(
    description=f"Agentic task: {interaction.utterance[:100]}",
    task_type="AGENTIC_LOOP",
    action_name=self.get_class_name(),
    metadata={"skills": self.skills, "skills_source": self.skills_source},
) as task:
    await task.record_step("thinking", iteration=1)
    await task.record_step("tool_call", iteration=1, details={"count": 2})
    await task.record_step("tool_result", iteration=1, details={"duration_ms": 150})
    await task.complete(status="completed", summary="Found 3 issues")
```

See `docs/task-tracking.md` for the shared lifecycle, status model, and callback events.

## Message Format Conversion

The agentic loop maintains messages internally in OpenAI-compatible format. When the model provider is Anthropic, `LoopContext.convert_for_provider()` transforms them:

**OpenAI format** (internal):
```json
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": null, "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{\"path\": \"...\"}"}}]},
  {"role": "tool", "tool_call_id": "c1", "content": "file contents..."}
]
```

**Anthropic format** (converted):
```json
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": [
    {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "..."}}
  ]},
  {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "c1", "content": "file contents..."}
  ]}
]
```

Key differences:
- OpenAI: `tool_calls` at message level, `role: "tool"` for results
- Anthropic: `tool_use` content blocks, tool results grouped into `user` messages with `tool_result` blocks
- Consecutive tool results are merged into a single Anthropic `user` message

## Context Window Management

`LoopContext.maybe_truncate()` keeps the context window from growing unbounded during long loops:

- Keeps the **system message**, **first user message**, and **last message** always
- Keeps the **last N tool result messages** in full (configurable via `max_full_tool_results`, default 10)
- Older tool results are replaced with `"(Earlier tool result summarized)"`
- Individual tool results exceeding `max_tool_result_tokens` estimated tokens (via `estimate_tokens()`) are truncated to `tool_result_truncation_chars` characters with a `... (truncated)` suffix
- Handles **both OpenAI and Anthropic message formats**: detects `role: "tool"` (OpenAI) and `type: "tool_result"` content blocks inside user messages (Anthropic)

## Forced Termination

When iteration or duration limits are reached:

1. Appends: `"You have reached the maximum number of steps. Provide your best final answer now."`
2. Removes `thinking` config from model kwargs
3. Makes one final LLM call **without tools** (forces text-only response)

## Extending the Thinking Agent

### Adding a New Built-in Skill

1. Create a directory under `jvagent/skills/`:

```
jvagent/skills/my_skill/
  SKILL.md
  my_tool.py   # optional
```

2. Write SKILL.md with frontmatter and SOP:

```markdown
---
name: my_skill
description: What this skill does.
allowed-tools:
  - my_tool
version: 1
tags: [custom]
---

## Workflow
1. Step one
2. Step two
```

3. Optionally add Python tool modules (must export `get_tool_definition()` + `execute()`).

4. Agents using `skills_source: both` or `skills_source: builtin` will discover it automatically.

### Adding an App-Local Skill

1. Create a directory under the agent's `skills/` folder:

```
agents/jvagent/my_agent/skills/my_custom_skill/
  SKILL.md
```

2. Or use the CLI:

```bash
jvagent skill add jvagent/my_agent my_custom_skill --description "Custom skill"
```

3. Add the skill name to the `skills` selector in agent.yaml:

```yaml
skills:
  - "my_custom_skill"
```

### Overriding a Built-in Skill

Place an app-local skill with the same `name` in the frontmatter. The app-local bundle overrides the built-in:

```yaml
# agents/jvagent/my_agent/skills/research/SKILL.md
---
name: research
description: Custom research workflow for our domain.
---
...
```

### Adding a New MCP Server

1. Add a server entry to `jvagent/mcp.context.servers` with a unique `name`:

```yaml
- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: "websearch"
        transport: "streamable_http"
        url: "http://localhost:3001/mcp"
        mcp_connect_timeout: 10
        mcp_call_timeout: 30
        tools: "-all"
        denied_tools: []
```

2. Add the server `name` to `tool_servers`:

```yaml
- action: jvagent/skill_interact_action
  context:
    tool_servers: ["filesystem", "websearch"]
```

### Creating a Custom SkillInteractAction Subclass

```python
from jvagent.action.skill import SkillInteractAction

class CustomThinkingAction(SkillInteractAction):
    max_iterations: int = attribute(default=50)
```

Overridable methods:
- `_build_model_kwargs()` -- customize model parameters
- `_run_agentic_loop()` -- customize the agentic loop
- `_call_model()` -- customize model invocation
- `_force_termination()` -- customize limit behavior

For deeper customization of message handling, subclass or replace the extracted modules:
- **SkillCatalog** -- customize skill discovery, search, or rendering
- **LoopContext** -- customize message building, truncation, or format conversion
- **StuckDetector** -- customize stuck detection thresholds or behavior
- **ToolExecutor** -- customize tool dispatch or skill activation

## Error Handling

### Tool Dispatch Errors

| Scenario | Behavior |
|----------|----------|
| Unknown tool name | Returns `Error: Tool 'X' is not available. Available tools: [...]` |
| Missing required params | Returns `Error: Validation failed: ...` |
| Tool call timeout | Returns `Error: Tool call timed out after Ns` |
| Tool execution failure | If `sanitize_errors=True`: `Error: Tool execution failed: X`. If `False`: includes the full exception message. |
| MCP server down | ToolExecutor logs warning during `initialize()`; tools from that server are unavailable |
| Skill not registered | `activate_skill()` raises `ToolDispatchError` |
| Skill already active | `activate_skill()` returns `[]` (idempotent) |
| Tool not in `allowed-tools` | Skipped during activation; not registered |
| Tool file outside allowed paths | `activate_skill()` logs warning; tool skipped (security) |

### Agentic Loop Errors

| Scenario | Behavior |
|----------|----------|
| No model action found | `get_model_action(required=True)` raises at runtime |
| No conversation | Action logs warning and calls `unrecord_action_execution()` |
| No tools available | Logs warning, proceeds in reasoning-only mode |
| Max iterations reached | Forced termination call (no tools, no thinking) |
| Max duration reached | Forced termination call (no tools, no thinking) |
| Invalid `skills_source` | Logs warning, returns empty skill set |
| Exception in loop | Logs error, task context marks failed, unrecords action |

## Testing

```bash
# Run all skill action tests
pytest tests/action/skill/ -v

# Run specific test files
pytest tests/action/skill/test_skill_interact_action.py -v
pytest tests/action/skill/test_skill_catalog.py -v
pytest tests/action/skill/test_loop_context.py -v
pytest tests/action/skill/test_stuck_detector.py -v
pytest tests/action/skill/test_agentic_loop.py -v
pytest tests/action/skill/test_tool_executor.py -v
pytest tests/action/skill/test_tool_registry.py -v
pytest tests/action/skill/test_progressive_disclosure.py -v
pytest tests/action/skill/test_skill_bundle_discovery.py -v
pytest tests/action/skill/test_skill_resolution_app_root.py -v
pytest tests/action/skill/test_prompts_snapshots.py -v
pytest tests/action/skill/test_action_resolver.py -v
pytest tests/action/skill/test_thinking_action_lifecycle.py -v
pytest tests/action/skill/test_anthropic_thinking.py -v
pytest tests/memory/services/test_task_service.py -v

# Run skill resolver tests
pytest tests/scaffold/test_skill_resolve.py -v
```

### Test Coverage Areas

| File | Coverage |
|------|----------|
| `test_skill_interact_action.py` | `_build_model_kwargs`, message construction, `_force_termination`, healthcheck, `_should_retry_for_skill_first` |
| `test_skill_catalog.py` | `SkillCatalog.discover()`, `format_index_entry()`, `render_catalog()`, `render_system_prompt_section()`, `check_activation_limit()`, `validate_requirements()`, `get_response_mode_override()`, `search()`, `_normalize_tokens()`, `_compute_relevance()`, multilingual matching |
| `test_loop_context.py` | `build_initial_messages()` (async), `maybe_truncate()` (OpenAI + Anthropic), `convert_for_provider()`, `build_assistant_content()`, `parse_tool_arguments()` |
| `test_stuck_detector.py` | `StuckDetector.record()`, correction limits, `reset()`, `_build_signature()` |
| `test_agentic_loop.py` | Skill-first retry logic, stuck detector integration, catalog search integration, Anthropic format conversion |
| `test_tool_executor.py` | Tool registration, pattern filters, dispatch (local, unknown, validation, timeout, error sanitization, concurrent), MCP registration + dispatch, skill bundle registration + activation, namespaced tool names |
| `test_tool_registry.py` | `ToolRegistry` register/remove/get/names, `ToolHandle` frozen dataclass, collision-safe namespacing with prefix |
| `test_progressive_disclosure.py` | Skill tools hidden until activation, `allowed_tools` enforcement, namespaced tool names |
| `test_skill_bundle_discovery.py` | Selector filtering (`-all`, lists, globs), `denied_skills`, `SkillCatalog.discover()` |
| `test_skill_resolution_app_root.py` | `get_app_root()` integration with skill resolution |
| `test_prompts_snapshots.py` | Prompt template snapshot tests (regression guard) |
| `test_task_service.py` | Shared task service lifecycle, reserve/complete/fail, step metadata |
| `test_anthropic_thinking.py` | `_build_payload` with/without thinking, temperature omission, max_tokens auto-adjustment, `_extract_result_fields` with thinking blocks, `ModelActionResult` thinking defaults |

## Troubleshooting

### No Skill Bundles Resolved

**Cause**: `skills` is `None`/empty, or `skills_source` is `"none"`.

**Fix**: Set `skills: -all` or `skills: ["desired_skill"]` and verify `skills_source` is `"both"`, `"builtin"`, or `"app"`.

### Skill Tool Not Available After `read_skill`

**Cause**: The tool module may not export `get_tool_definition()` and `execute()`, or the tool name may not be in the `allowed-tools` whitelist.

**Fix**: Verify the `.py` file exports both functions and the tool name matches an entry in `allowed-tools` (or omit `allowed-tools` to allow all tools).

### Tool Name Collision Across Skills

**Cause**: Multiple skill bundles have `.py` files with the same name (e.g., `search.py` in both `web_search` and `pageindex_search`).

**Fix**: This is handled automatically by tool name namespacing. Each skill's tools are registered with a `{skill_name}__` prefix (e.g., `web_search__search`, `pageindex_search__search`). SKILL.md workflow instructions should use namespaced tool names.

### MCP Connection Failures (BrokenResourceError)

**Cause**: The MCP stdio transport uses a subprocess with task groups. AsyncExitStack is incompatible with the SDK's task group lifecycle.

**Fix**: Verify `MCPClientWrapper._connect_stdio()` uses the background task pattern (not AsyncExitStack for stdio).

### Tool Results Treated as Errors

**Cause**: The MCP Python SDK exposes the error flag as `isError` (camelCase). If `_normalize_call_result()` reads `is_error` (snake_case) and defaults to `True`, every successful MCP tool call surfaces as `Tool execution failed: <tool_name>`.

**Fix**: `_normalize_call_result()` in `jvagent/action/mcp/mcp_action.py` must read `isError` first (with `is_error` as a fallback) and default to `False` when neither is present.

### Agent Returns "NO RESPONSE" / 0 Tokens

**Cause**: `_call_model()` must call `model_action.query_messages()` with a message list, not `model_action.query()` which expects a prompt string.

**Fix**: Verify `_call_model()` calls `model_action.query_messages(messages=..., stream=False, system=..., history=..., tools=..., calling_action_name=..., prompt_for_observability=..., **kwargs)`.

### App-Local Skill Not Overriding Built-in

**Cause**: The `name` field in the app-local SKILL.md frontmatter must match the built-in skill's name exactly.

**Fix**: Verify both SKILL.md files have the same `name` value in their frontmatter.

### Coroutine Not Awaited

**Cause**: Async methods called without `await` (e.g., `self.get_action()` or `conversation.get_interaction_history()`).

**Fix**: Ensure all `async def` methods are called with `await`. LoopContext's `build_initial_messages()` and ActionResolver's `resolve()`/`validate_requirements()` are async.

## API Reference

### SkillInteractAction

```python
class SkillInteractAction(InteractAction):
    async def execute(visitor: InteractWalker) -> None
    async def healthcheck() -> bool

    # Protected (overridable)
    async def _run_agentic_loop(visitor, tool_executor, task_handle, discovered_skills) -> tuple[str, str, int]
    def _build_model_kwargs() -> Dict[str, Any]
    async def _call_model(messages, tools, visitor, model_kwargs) -> Any
    async def _force_termination(messages, tools, visitor, model_kwargs) -> str
```

### SkillCatalog

```python
class SkillCatalog:
    def __init__(discovered_skills: Dict[str, Dict[str, Any]])
    @classmethod async def discover(cls, visitor, skills_selector, skills_source, denied_skills=None) -> SkillCatalog

    @property skills -> Dict[str, Dict[str, Any]]
    @property is_empty -> bool

    def format_index_entry(skill_name, skill_data) -> str
    def render_catalog() -> str
    def render_system_prompt_section() -> str
    def check_activation_limit(skill_name, activated_skills, max_activations) -> Optional[str]
    async def validate_requirements(skill_name, action_resolver) -> Optional[str]
    def get_response_mode_override(activated_skills, default_mode) -> str
    def search(query, top_k=5) -> str
```

### LoopContext + LoopContextConfig

```python
@dataclass
class LoopContextConfig:
    max_full_tool_results: int = 10
    max_tool_result_tokens: int = 400
    tool_result_truncation_chars: int = 500
    history_limit: int = 5

class LoopContext:
    def __init__(config: LoopContextConfig)
    async def build_initial_messages(system_prompt, utterance, conversation, interaction, skill_index_section=None) -> List[Dict[str, Any]]
    def maybe_truncate(messages) -> List[Dict[str, Any]]

    @staticmethod convert_for_provider(messages, provider) -> List[Dict[str, Any]]
    @staticmethod build_assistant_content(model_result) -> Dict[str, Any]
    @staticmethod parse_tool_arguments(arguments) -> Dict[str, Any]
```

### StuckDetector + StuckDetectorConfig

```python
@dataclass
class StuckDetectorConfig:
    window_size: int = 3
    max_corrections: int = 2

class StuckDetector:
    def __init__(config: StuckDetectorConfig)
    @property corrections -> int
    def record(tool_calls) -> Optional[str]   # returns STUCK_DETECTION_PROMPT or "FORCE_TERMINATE"
    def reset() -> None
```

### LoopState Enum

```python
class LoopState(str, Enum):
    MODEL = "MODEL"         # Currently calling the LLM
    TOOLS = "TOOLS"          # Dispatching tool calls
    TERMINATE = "TERMINATE"  # Loop ending
```

### TerminationReason Enum

```python
class TerminationReason(str, Enum):
    COMPLETED = "completed"      # LLM returned final text response
    ITER_CAP = "max_iterations"  # Hit max_iterations limit
    TIME_CAP = "timed_out"       # Hit max_duration_seconds limit
    ERROR = "failed"             # Unrecoverable error
```

### ToolRegistry & ToolHandle (internal)

```python
@dataclass(frozen=True)
class ToolHandle:
    name: str              # Registered name (may be namespaced)
    fq_name: str           # Fully-qualified name (e.g. "mcp:filesystem:read_file")
    source: str             # Source tag ("mcp", "local", "dynamic")
    schema: Dict[str, Any] # JSON Schema for parameters
    dispatch: Callable     # Handler for tool execution

class ToolRegistry:
    def register(*, name, source, schema, dispatch, fq_name=None, prefix=None) -> ToolHandle
    def remove(name) -> None
    def get(name) -> Optional[ToolHandle]
    def names() -> list[str]
```

> **Note:** `ToolRegistry` and `ToolHandle` are internal to `ToolExecutor` and are **not** exported from the `jvagent.action.skill` package.

### ToolExecutor

```python
class ToolExecutor:
    def __init__(call_timeout=60.0, validate_calls=True, max_concurrent_calls=5, sanitize_errors=True, allowed_tool_paths=None)

    async def initialize(visitor, tool_servers=None, allowed_tool_patterns=None, denied_tool_patterns=None, local_tools_paths=None) -> None
    def register_local_tool(name, handler, description, parameters) -> ToolDefinition
    def register_dynamic_tool(name, tool_def_dict, handler) -> None
    def register_skill_bundle(skill_name, dir_path, tool_files=None, allowed_tools=None) -> None
    async def activate_skill(skill_name) -> List[str]
    async def dispatch(tool_calls, visitor=None) -> List[Dict[str, Any]]
    def get_tools_list() -> List[Dict[str, Any]]
    def get_tool_names() -> Set[str]
    async def cleanup() -> None
```

### TaskService

```python
class TaskService:
    async def start(..., task_type, metadata=None, trigger_at=None, trigger_condition=None) -> TaskHandle
    async def record_step(task_id, step_type, iteration=0, details=None) -> bool
    async def update_metadata(task_id, **patch) -> bool
    async def complete(task_id, status="completed", summary=None) -> bool
    async def fail(task_id, error, status="failed") -> bool
    async def reserve(task_id) -> bool
    def track(...) -> AsyncContextManager[TaskHandle]
```

### Skill Resolver

```python
# jvagent.scaffold.skill_resolve

def parse_skill_bundle(skill_dir, *, source) -> Optional[Dict[str, Any]]
def resolve_builtin_skills() -> Dict[str, Dict[str, Any]]
def resolve_agent_skills(app_root, namespace, agent_name) -> Dict[str, Dict[str, Any]]
def resolve_merged_skill_bundles(app_root, namespace, agent_name, *, include_builtin=True) -> Dict[str, Dict[str, Any]]
def apply_skill_selector(bundles, selector, denied=None) -> Dict[str, Dict[str, Any]]
def list_builtin_skill_names() -> List[str]
def list_agent_skill_names(app_root, namespace, agent_name) -> List[str]
```

## See Also

- [SKILL.md Bundles](../../skills/README.md) -- Built-in skill catalog layout
- [MCPAction README](../mcp/README.md) -- MCP server configuration and tool discovery
- [InteractAction README](../interact/README.md) -- Base class API and walker traversal
- [PersonaAction README](../persona/README.md) -- Response generation for non-agentic interactions
- [InteractRouter README](../router/README.md) -- Intent-based routing and anchor configuration

---

**Last Updated**: April 19, 2026
**Version**: 0.0.1
**Status**: Production Ready