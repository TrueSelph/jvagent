# ThinkingInteractAction

`ThinkingInteractAction` runs a long-lived think-act-observe loop for multi-step tasks with tool use, progressive skill loading, and per-conversation task tracking.

## Overview

When activated by the InteractRouter, this action:

1. Resolves **skill bundles** from built-in and app-local catalogs
2. Initializes a **ToolExecutor** with tools from configured MCP servers
3. Runs the agentic loop: LLM thinks, calls tools, observes results, and iterates
4. Tracks multi-step progress via shared **TaskService** on `Conversation.active_tasks`
5. Streams intermediate progress (thinking, tool calls, tool results) as thought-track messages via `publish_thought()`
6. Publishes the final response when the loop completes

### Key Capabilities

- **Progressive skill disclosure**: Skills are exposed via a `read_skill` tool. Their Python tool modules are only loaded when the LLM calls `read_skill`, keeping the tool list lean until specialization is needed.
- **Deterministic tool dispatch**: The LLM decides which tool to call; ToolExecutor dispatches directly via `MCPClientWrapper.call_tool()`, bypassing `MCPAction.fulfill()` NL-to-tool mapping.
- **Multi-provider support**: OpenAI and Anthropic message formats handled transparently via `_convert_messages_for_provider()`.
- **Extended thinking**: Anthropic extended thinking with configurable token budget, streamed as `thought_type="reasoning"`.
- **Context window management**: Old tool results automatically summarized to stay within limits.
- **Forced termination**: Graceful summarization when iteration or duration limits are reached.

## Architecture

```
jvagent/action/thinking/
  __init__.py                      # Package exports
  thinking_interact_action.py      # ThinkingInteractAction (InteractAction)
  tool_executor.py                 # ToolExecutor (runtime helper)
  prompts.py                       # System prompt templates
  info.yaml                        # Package: jvagent/thinking_interact_action

jvagent/scaffold/
  skill_resolve.py                 # Skill bundle resolution and filtering

jvagent/skills/                    # Built-in skill catalog
  code_review/SKILL.md
  research/SKILL.md
  triage/SKILL.md + prioritize_findings.py
```

### Component Call Graph

```
InteractRouter
  |
  v (activates by weight/classification)
ThinkingInteractAction.execute(visitor)
  |
  +--->_discover_skill_bundles(visitor)
  |       Resolves bundles via jvagent.scaffold.skill_resolve
  |       Applies skills/denied_skills/skills_source filters
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
          +--->_build_model_kwargs()
          +--->_build_initial_messages(visitor, discovered_skills)
          |       Injects SKILL_INDEX_INTRO + skill index
          +---> LOOP (iteration < max, elapsed < timeout):
          |       |
          |       +--->tools = tool_executor.get_tools_list()  # re-fetched each iteration
          |       +--->_call_model(messages, tools, visitor, kwargs)
          |       +--->IF thinking_content: publish_thought(thought_type="reasoning")
          |       +--->IF no tool_calls: final_response -> BREAK
          |       +--->IF tool_calls:
          |       |       publish(category="user") any mid-loop assistant text
          |       |         (so it is rendered to the user AND appended to
          |       |          interaction.response for conversation history)
          |       |       publish_thought(thought_type="tool_call") per call
          |       |       _build_assistant_content() -> append
          |       |       tool_executor.dispatch() -> result messages
          |       |       publish_thought(thought_type="tool_result") per result
          |       |       Append results, truncate if needed
          |       |
          |       +--->IF at limit: _force_termination()
          |
          +--->publish(visitor, final_response, streaming_complete=True)
          +--->task.complete(status, summary)
          +--->tool_executor.cleanup()
```

## Skill Sources

Skill bundles are resolved from two sources:

1. **Built-in reusable catalog** shipped with jvagent (`jvagent/skills/*`)
2. **App-local custom skills** under `agents/<namespace>/<agent_id>/skills/*/SKILL.md`

Resolution precedence is deterministic: **app-local overrides built-in** when the same skill name exists in both places.

### Per-Agent Skill Controls

`ThinkingInteractAction` supports explicit skill exposure controls in `agent.yaml`:

```yaml
- action: jvagent/thinking_interact_action
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
- `version`, `license`, `tags` (optional metadata)

### Progressive Disclosure Flow

1. At action start, bundles are resolved from the configured source (`builtin`, `app`, or `both`).
2. The resolved set is filtered by `skills` and `denied_skills`.
3. Each bundle is **registered** on the ToolExecutor but its Python tools are **not yet exposed** to the LLM.
4. The loop injects a `read_skill` tool and a skill index in the system prompt.
5. When the model calls `read_skill(skill_name=...)`, the action:
   - **activates** the bundle's Python tool modules via `tool_executor.activate_skill()`
   - returns the full SOP content and a list of newly available tools
6. Tool definitions are re-fetched each iteration, so newly activated skill tools become available on the next turn.

This mirrors the Claude Code skill model: the LLM discovers capabilities on demand rather than being overwhelmed with every tool at once.

## The Agentic Loop

The core algorithm in `_run_agentic_loop()`:

```
1. Build model kwargs (base config + thinking config)
2. Compose initial messages (system prompt + skill index + history + user utterance)
3. LOOP:
   a. Check iteration and duration limits
   b. Re-fetch tool list (includes newly activated skill tools)
   c. Record "thinking" step on TaskService task handle
   d. Call LLM with current messages + tools
   e. Publish reasoning thought (if Anthropic extended thinking)
   f. If no tool_calls -> extract text, BREAK
   g. If tool_calls present:
      i.    Record "tool_call" step
      ii.   If commit_intermediate_messages and the model emitted text
            alongside the tool calls, publish that text as
            category="user" so it is rendered to the user AND appended
            to interaction.response (keeps conversation history complete)
      iii.  Publish tool_call thought (thought_type="tool_call")
      iv.   Build assistant message, append to messages
      v.    Dispatch tool calls via ToolExecutor
      vi.   Publish tool_result thought (thought_type="tool_result")
      vii.  Append tool result messages
      viii. Record "tool_result" step
      ix.   Truncate messages if context window too large
   h. Increment iteration
4. If limit hit without response: forced termination call
5. Publish final response (category="user", persisted to interaction.response)
```

## Configuration

All attributes can be overridden via `context` in agent.yaml.

### ThinkingInteractAction Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `weight` | int | `-60` | Execution weight (after InteractRouter, before Persona) |
| `max_iterations` | int | `25` | Hard cap on think-act-observe cycles |
| `max_duration_seconds` | float | `300.0` | Wall-clock timeout (seconds) |
| `thinking_budget_tokens` | int | `0` | Anthropic extended thinking budget (0 = disabled) |
| `model_action_type` | str | `"AnthropicLanguageModelAction"` | LanguageModelAction entity type |
| `model` | str | `"claude-sonnet-4-20250514"` | Model identifier |
| `model_temperature` | float | `0.3` | LLM temperature |
| `model_max_tokens` | int | `8192` | Max tokens for LLM generation |
| `skills` | Any | `None` | Skill selector: `"-all"` or list of names/globs or `None` |
| `denied_skills` | List[str] | `[]` | Subtractive filter on resolved bundles (supports globs) |
| `skills_source` | str | `"both"` | Bundle source: `"builtin"`, `"app"`, `"both"`, or `"none"` |
| `tool_servers` | List[str] | `[]` | Names of MCPAction instances providing tools |
| `allow_local_tools` | bool | `False` | Whether ToolExecutor can register local Python tools |
| `stream_thinking` | bool | `True` | Publish reasoning thoughts via `publish_thought()` |
| `stream_tool_progress` | bool | `True` | Publish tool_call and tool_result thoughts via `publish_thought()` |
| `commit_intermediate_messages` | bool | `True` | Publish (and persist) any text the model emits alongside tool calls as a `category="user"` message so the conversation history matches what the assistant said. |
| `relay_thoughts_to_channels` | bool | `False` | Relay thought messages to channel adapters only when adapters opt in (`deliver_thoughts=True`) |
| `max_full_tool_results` | int | `10` | Keep last N tool results in full; summarize older |
| `local_tools_path` | str | `None` | Absolute path to a directory of local .py tool modules |

### ToolExecutor Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `call_timeout` | float | `60.0` | Timeout in seconds for each individual tool call |
| `validate_calls` | bool | `True` | Validate tool calls against schema before dispatch |
| `max_concurrent_calls` | int | `5` | Maximum concurrent tool executions (semaphore) |
| `sanitize_errors` | bool | `True` | Replace internal error details with generic messages |

## Agent Wiring (agent.yaml)

A complete thinking agent requires: a LanguageModelAction, one or more MCP servers (optional but typical), and the ThinkingInteractAction itself. Skill bundles are resolved from the built-in catalog and/or app-local `skills/` directories.

### Full Example

```yaml
agent: jvagent/thinking_agent
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
      server_name: "filesystem"
      transport: "stdio"
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      mcp_connect_timeout: 15
      mcp_call_timeout: 60

  # ── Thinking Agent (Core Loop) ─────────────────
  - action: jvagent/thinking_interact_action
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
      thinking_budget_tokens: 0        # 0 = disabled (OpenAI)
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

- action: jvagent/thinking_interact_action
  context:
    enabled: true
    model_action_type: "AnthropicLanguageModelAction"
    model: "claude-sonnet-4-20250514"
    thinking_budget_tokens: 10000
    stream_thinking: true
```

When `thinking_budget_tokens > 0`, the action injects `thinking: {type: "enabled", budget_tokens: N}` into the API call. Anthropic requires `max_tokens >= budget_tokens + 1`; the action auto-adjusts if needed. When thinking is enabled, Anthropic omits `temperature` from the payload (a provider requirement).

### Minimal (Reasoning-Only, No Tools or Skills)

```yaml
- action: jvagent/thinking_interact_action
  context:
    enabled: true
    weight: -60
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1"
    max_iterations: 10
    # No tool_servers, no skills -> reasoning-only mode
```

## MCP Tool Integration

ThinkingInteractAction uses MCP servers as tool providers. Configure one `jvagent/mcp` action per server, then reference them by `server_name` in `tool_servers`.

### How Tools Are Discovered

1. ToolExecutor finds the MCPAction by `server_name` via `agent.get_actions()` iteration
2. Calls `mcp_action.get_tools_cached()` to get the tool list (cached per session)
3. Registers each tool in ToolManager with name, description, and input schema
4. Maps `tool_name -> ("mcp", mcp_action)` in the handlers registry

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

ToolExecutor scans all `.py` files, imports `get_tool_definition()` and `execute()`, and registers them.

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

### 3. Skill Bundle Tools (Progressive)

Tools inside a skill bundle directory are hidden until the LLM calls `read_skill`. See [Progressive Disclosure Flow](#progressive-disclosure-flow).

## Built-in Skills

jvagent ships with three built-in skill bundles:

| Skill | Description | Tools |
|-------|-------------|-------|
| `code_review` | Review code for correctness, security, and maintainability | (SOP only) |
| `research` | Investigate a topic with evidence-first synthesis and citations | (SOP only) |
| `triage` | Rapidly triage issues by severity, impact, and next action | `prioritize_findings` |

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
| `version` | Optional | int/str | Version number for tracking |
| `license` | Optional | str | License identifier |
| `tags` | Optional | list[str] | Tags for categorization |

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

If `allowed-tools` is set in the frontmatter, only tools whose names match the whitelist are activated.

## CLI Commands

jvagent provides CLI commands for managing skill bundles:

### `jvagent skill add`

Create a SKILL.md bundle skeleton for an agent:

```bash
jvagent skill add <agent_ref> <skill_name> [--description TEXT] [--force]
```

- `agent_ref`: Agent reference (e.g. `jvagent/thinking_agent`)
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

When `model_action_type` is `AnthropicLanguageModelAction` and `thinking_budget_tokens > 0`:

- The action injects `thinking: {type: "enabled", budget_tokens: N}` into model kwargs
- Anthropic's `_build_payload()` omits `temperature` (required by the API when thinking is on)
- `max_tokens` is auto-adjusted to `>= budget_tokens + 1` if needed
- Thinking content is extracted from `model_result.thinking_content` and `thinking_tokens`
- If `stream_thinking=True`, thinking content is published as `category="thought"` with `thought_type="reasoning"`

**Note:** Extended thinking is Anthropic-only. Set `thinking_budget_tokens: 0` (default) for OpenAI providers.

## Streaming

ThinkingInteractAction publishes two logical tracks:

- **User track** (`category="user"`): end-user-facing assistant utterances. Both the final response and any mid-loop commentary the model emits alongside tool calls (when `commit_intermediate_messages=True`, the default) are published on this track and appended to `interaction.response` so the persisted conversation history matches what the user saw.
- **Thought track** (`category="thought"`): reasoning/tool telemetry for observability and specialized client rendering.

Thought track events include:

- `thought_type="reasoning"` with `segment_id="iter-{n}-reasoning"`
- `thought_type="tool_call"` with `segment_id="iter-{n}-call-{tool_name}-{idx}"`
- `thought_type="tool_result"` with `segment_id="iter-{n}-result-{tool_call_id}"`

Thought messages are never appended to `interaction.response`; they are stored in `interaction.agent_trace`. Clients can route thoughts to a separate panel by checking `category`, `thought_type`, and `segment_id`.

## Task Tracking

Thinking actions now use the shared `TaskService` (`visitor.tasks`) instead of a local tracker helper. Each run creates one `AGENTIC_LOOP` task scoped to the current conversation and guarantees terminal status through `async with visitor.tasks.track(...)`.

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

The agentic loop maintains messages internally in OpenAI-compatible format. When the model provider is Anthropic, `_convert_messages_for_provider()` transforms them:

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

`_maybe_truncate_messages()` keeps the context window from growing unbounded during long loops:

- Keeps the **system message**, **first user message**, and **last message** always
- Keeps the **last N tool result messages** in full (configurable via `max_full_tool_results`, default 10)
- Older tool results are replaced with `"(Earlier tool result summarized)"`

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

1. Add a `jvagent/mcp` action with a unique `server_name`:

```yaml
- action: jvagent/mcp
  context:
    enabled: true
    label: "Web Search MCP"
    server_name: "websearch"
    transport: "streamable_http"
    url: "http://localhost:3001/mcp"
    mcp_connect_timeout: 10
    mcp_call_timeout: 30
```

2. Add the `server_name` to `tool_servers`:

```yaml
- action: jvagent/thinking_interact_action
  context:
    tool_servers: ["filesystem", "websearch"]
```

### Creating a Custom ThinkingInteractAction Subclass

```python
from jvagent.action.thinking import ThinkingInteractAction

class CustomThinkingAction(ThinkingInteractAction):
    max_iterations: int = attribute(default=50)

    async def _build_initial_messages(self, visitor, discovered_skills=None):
        messages = await super()._build_initial_messages(visitor, discovered_skills)
        # Add custom context
        messages.insert(0, {"role": "system", "content": "Domain-specific instructions..."})
        return messages
```

Overridable methods:
- `_build_model_kwargs()` -- customize model parameters
- `_build_initial_messages(visitor, discovered_skills)` -- customize system prompt / history
- `_build_assistant_content(model_result)` -- customize message format
- `_maybe_truncate_messages(messages)` -- customize context window management
- `_force_termination(messages, tools, visitor, model_kwargs)` -- customize limit behavior
- `_discover_skill_bundles(visitor)` -- customize skill resolution

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
# Run all thinking action tests
pytest tests/action/thinking/ -v

# Run specific test files
pytest tests/action/thinking/test_tool_executor.py -v
pytest tests/action/thinking/test_thinking_interact_action.py -v
pytest tests/memory/services/test_task_service.py -v
pytest tests/action/thinking/test_anthropic_thinking.py -v
pytest tests/action/thinking/test_progressive_disclosure.py -v
pytest tests/action/thinking/test_skill_bundle_discovery.py -v

# Run skill resolver tests
pytest tests/scaffold/test_skill_resolve.py -v
```

### Test Coverage Areas

| File | Coverage |
|------|----------|
| `test_tool_executor.py` | Tool registration, pattern filters, dispatch (local, unknown, validation, timeout, error sanitization, concurrent), MCP registration + dispatch, skill bundle registration + activation |
| `test_thinking_interact_action.py` | `_build_model_kwargs`, `_build_initial_messages`, message truncation, `_build_assistant_content`, `_convert_messages_for_provider`, `_force_termination`, `_discover_skill_bundles`, healthcheck |
| `test_progressive_disclosure.py` | Skill tools hidden until activation, `allowed_tools` enforcement |
| `test_skill_bundle_discovery.py` | Selector filtering (`-all`, lists, globs), `denied_skills` |
| `test_skill_resolve.py` | `resolve_builtin_skills`, `resolve_agent_skills`, `resolve_merged_skill_bundles`, `apply_skill_selector`, frontmatter parsing, override precedence |
| `test_task_service.py` | Shared task service lifecycle, reserve/complete/fail, step metadata |
| `test_anthropic_thinking.py` | `_build_payload` with/without thinking, temperature omission, max_tokens auto-adjustment, `_extract_result_fields` with thinking blocks, `ModelActionResult` thinking defaults |

## Troubleshooting

### No Skill Bundles Resolved

**Cause**: `skills` is `None`/empty, or `skills_source` is `"none"`.

**Fix**: Set `skills: -all` or `skills: ["desired_skill"]` and verify `skills_source` is `"both"`, `"builtin"`, or `"app"`.

### Skill Tool Not Available After `read_skill`

**Cause**: The tool module may not export `get_tool_definition()` and `execute()`, or the tool name may not be in the `allowed-tools` whitelist.

**Fix**: Verify the `.py` file exports both functions and the tool name matches an entry in `allowed-tools` (or omit `allowed-tools` to allow all tools).

### MCP Connection Failures (BrokenResourceError)

**Cause**: The MCP stdio transport uses a subprocess with task groups. AsyncExitStack is incompatible with the SDK's task group lifecycle.

**Fix**: Verify `MCPClientWrapper._connect_stdio()` uses the background task pattern (not AsyncExitStack for stdio).

### Tool Results Treated as Errors

**Cause**: The MCP Python SDK exposes the error flag as `isError` (camelCase). Earlier versions of `_normalize_call_result()` read `is_error` and defaulted to `True`, which caused every successful MCP tool call to surface as `Tool execution failed: <tool_name>`.

**Fix**: `_normalize_call_result()` in `jvagent/action/mcp/mcp_action.py` must read `isError` (with `is_error` as a fallback) and default to `False` when neither is present.

### Agent Returns "NO RESPONSE" / 0 Tokens

**Cause**: `_call_model()` was calling `model_action.query()` which expects a prompt string, not a message list.

**Fix**: The action calls `model_action._query(messages, tools=tools, **kwargs)` directly.

### App-Local Skill Not Overriding Built-in

**Cause**: The `name` field in the app-local SKILL.md frontmatter must match the built-in skill's name exactly.

**Fix**: Verify both SKILL.md files have the same `name` value in their frontmatter.

## API Reference

### ThinkingInteractAction

```python
class ThinkingInteractAction(InteractAction):
    async def execute(visitor: InteractWalker) -> None
    async def healthcheck() -> bool

    # Protected (overridable)
    async def _discover_skill_bundles(visitor) -> Dict[str, Dict[str, Any]]
    async def _run_agentic_loop(visitor, tool_executor, task_handle, discovered_skills) -> Optional[str]
    def _build_model_kwargs() -> Dict[str, Any]
    async def _build_initial_messages(visitor, discovered_skills=None) -> List[Dict[str, Any]]
    async def _call_model(messages, tools, visitor, model_kwargs) -> ModelActionResult
    def _convert_messages_for_provider(messages, provider) -> List[Dict[str, Any]]
    async def _force_termination(messages, tools, visitor, model_kwargs) -> str
    def _build_assistant_content(model_result) -> Dict[str, Any]
    def _parse_tool_arguments(arguments) -> Dict[str, Any]
    def _maybe_truncate_messages(messages) -> List[Dict[str, Any]]
```

### ToolExecutor

```python
class ToolExecutor:
    def __init__(call_timeout=60.0, validate_calls=True, max_concurrent_calls=5, sanitize_errors=True)

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

**Last Updated**: April 18, 2026
**Version**: 0.0.1
**Status**: Production Ready