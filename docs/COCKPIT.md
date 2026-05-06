# Cockpit Architecture

## Overview

The cockpit pattern grants the language model full agency over the agent's runtime services. Instead of a hardcoded pipeline that dictates *when* and *how* the model interacts with the system, the cockpit exposes every harness service and action capability as a **tool** — first-class callables that the model can invoke as it sees fit.

The model is the pilot. Tools are the controls. Skills are the flight plan.

## Architecture

```
POST /agents/{agent_id}/interact
  │
  ├─ 1. Bootstrap (InteractWalker)
  │     └─ Resolve User → Conversation → Interaction
  │
  ├─ 2. Route (CockpitRouter — lightweight pre-cockpit LLM call)
  │     └─ Classify posture: RESPOND | SUPPRESS | DEFER
  │     └─ Select relevant skills from the catalog
  │
  └─ 3. CockpitEngine (walker-revisit pattern)
        │
        ├─ First visit: initialize()
        │   ├─ Build system prompt (agent identity + skill instructions)
        │   └─ Assemble full tool set:
        │       ├─ Harness service tools (memory, response, task, conversation, skill)
        │       └─ Action tools (via Action.get_tools() from enabled actions)
        │
        └─ Each walker visit: step()
            ├─ model.query(messages, tools=all_tools)
            ├─ if tool_calls → dispatch → persist state → prepend self to walk path
            └─ if no tool_calls → deliver final response → done
```

The cockpit engine does **one model call per walker visit**. When the model returns tool calls, the action persists `CockpitState` on `visitor._skill_state` and re-adds itself to the walk path via `visitor.prepend([self])`. The next walker visit restores state and runs the next step. This gives the walker natural visibility into each iteration — stream commits, action recording, and access control checks happen per step.

## Tool Categories

### Harness Service Tools

These tools expose the agent's internal services to the model. They are always available.

| Tool | Source | Purpose |
|------|--------|---------|
| `memory_get_history` | `action/cockpit/memory_tools.py` | Retrieve past interactions |
| `memory_get_user_info` | `action/cockpit/memory_tools.py` | Get user profile |
| `memory_update_user_model` | `action/cockpit/memory_tools.py` | Store facts/preferences about the user |
| `memory_set_preference` | `action/cockpit/memory_tools.py` | Set conversation-scoped preference |
| `response_publish` | `action/cockpit/response_tools.py` | Send a message to the user (finalize=True ends the turn) |
| `response_emit_thought` | `action/cockpit/response_tools.py` | Emit reasoning trace |
| `response_deliver_via_persona` | `action/cockpit/response_tools.py` | Polish output via PersonaAction |
| `task_create_plan` | `action/cockpit/task_tools.py` | Create a structured task plan |
| `task_update_step` | `action/cockpit/task_tools.py` | Mark step status |
| `task_get_status` | `action/cockpit/task_tools.py` | Check current progress |
| `task_add_step` | `action/cockpit/task_tools.py` | Add a step mid-execution |
| `conversation_search` | `action/cockpit/conversation_tools.py` | Search conversation history |
| `conversation_summarize` | `action/cockpit/conversation_tools.py` | Summarize recent exchanges |
| `skill_list` | `action/cockpit/skill_tools.py` | List installed skills |
| `skill_search` | `action/cockpit/skill_tools.py` | Search skills by keyword |
| `skill_read` | `action/cockpit/skill_tools.py` | Read full skill instructions |
| `artifact_add` / `artifact_get` / `artifact_update` / `artifact_delete` / `artifact_search` | `action/cockpit/artifact_tools.py` | Session-scoped structured data on `Interaction.artifacts` (pruned with the interaction) |
| `cockpit_search` (engine) | `action/cockpit/search_tools.py` | Unified capability search across skills + tools |
| `cockpit_search` (router, opt-in) | `action/cockpit/search_tools.py` | Same tool with `permitted_kinds = {skills, interact_actions, tools}` to inform the processing gate |

### Action Tools

Each `Action` subclass exposes its capabilities as `Tool` instances via `get_tools()`. These are collected from all enabled actions on the agent and registered with an `action__` prefix.

| Action | Tools Exposed |
|--------|--------------|
| `PageIndexAction` | `action__pageindex__search`, `action__pageindex__assimilate`, `action__pageindex__list`, `action__pageindex__delete` |
| `SerperWebSearchAction` | `action__web_search__search` |
| `GoogleGmailAction` | `action__gmail__send`, `action__gmail__search` |
| `GoogleCalendarAction` | `action__calendar__list_events`, `action__calendar__create_event` |
| `GoogleDriveAction` | `action__google_drive__list`, `action__google_drive__upload` |
| `GoogleSheetsAction` | `action__google_sheets__read`, `action__google_sheets__update`, `action__google_sheets__create` |
| `MCPAction` | `action__mcp_{server}__{tool}` for every configured MCP server |
| `MicrosoftOutlookMailAction` | `action__outlook__send`, `action__outlook__search` |
| `MicrosoftOneDriveAction` | `action__onedrive__list`, `action__onedrive__upload` |
| `MicrosoftExcelAction` | `action__excel__read`, `action__excel__update`, `action__excel__create` |

### Skill Tools

Skills remain as self-contained Claude-style directories (`SKILL.md` + optional `scripts/*.py`). Tool scripts in skill directories are loaded and registered alongside action tools with namespace prefixing (`{skill_name}__{tool_name}`).

## Walker-Revisit Pattern

Instead of an internal `while` loop, `CockpitEngine.step()` executes exactly one model call. The action controls iteration:

1. **Fresh visit**: Route, initialize engine, call `step()`. If tool calls: save `CockpitState` → `visitor.prepend([self])` → return. If text: deliver.
2. **Revisit**: Restore engine from `CockpitState`, call `step()`. Same logic.
3. **Termination**: When the model produces text (no tool calls), `response_publish(finalize=True)`, or a budget limit is hit, the action delivers the response and clears state.

`CockpitState` holds: `messages`, `iteration`, `activated_skills`, `started_at`, `tools_serialized`, `recent_tool_names`.

### Stuck Detection

The engine tracks `recent_tool_names` and uses Jaccard similarity across a configurable window (`stuck_detection_window`, default 3). When the same tool-call pattern repeats beyond `stuck_intent_jaccard_threshold` (default 0.7), it returns `CockpitStepResult(status="stuck")`.

## Tool Execution Engine

The `ToolExecutionEngine` is the single dispatch point for all tool calls. It:

1. Receives raw tool-call dicts from `ModelActionResult.tool_calls`
2. Looks up the matching `Tool` in the `ToolRegistry`
3. Calls `Tool.call(**args)` with a configurable timeout
4. Collects results with observability envelopes (`ToolExecutionEnvelope`)
5. Serializes results as tool-result messages for the next LLM iteration

## Plugging Into the Interact Pipeline

`CockpitInteractAction` is a standard `InteractAction` (weight: -200) that plugs into the `InteractWalker` pipeline:

```yaml
actions:
  - action: jvagent/cockpit
    context:
      enabled: true
      model_action_type: AnthropicLanguageModelAction
      model: claude-sonnet-4-20250514
      skills: [pageindex_search, web_search, research]
      max_iterations: 25
      response_mode: publish
```

The interact pipeline (`InteractWalker` → `Actions` → sorted `InteractAction` chain by weight) is fully preserved. Other actions (access control, WhatsApp adapters, etc.) continue to compose in the chain.

## Module Structure

The cockpit module at `jvagent/action/cockpit/` is self-contained — it imports only from `jvagent.action.router`, `jvagent.tooling`, and core modules.

| File | Purpose |
|------|---------|
| `cockpit_interact_action.py` | Main action (extends InteractAction) |
| `engine.py` | CockpitEngine: initialize() + step() |
| `context.py` | CockpitContext, CockpitResult, CockpitStepResult, CockpitState |
| `config.py` | CockpitConfig |
| `registry.py` | assemble_cockpit_tools() |
| `router.py` | CockpitRouter |
| `gates.py` | Conversational vs processing gate |
| `delivery.py` | Conversational and final response delivery |
| `shim.py` | CockpitVisitorShim |
| `skill_discovery.py` | Always-active skill detection |
| `memory_tools.py` | Memory harness tools (read + write) |
| `response_tools.py` | Response harness tools |
| `task_tools.py` | Task harness tools (TaskStore-backed) |
| `conversation_tools.py` | Conversation harness tools |
| `skill_tools.py` | Skill harness tools |

## Implementing New Action Tools

To make an action's capabilities available as cockpit tools, implement `get_tools()`:

```python
from jvagent.action.base import Action
from jvagent.tooling.tool import Tool

class MyAction(Action):
    async def get_tools(self) -> List[Tool]:
        return [
            Tool(
                name="my_action__do_thing",
                description="Does a specific thing.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "param": {"type": "string", "description": "A parameter."},
                    },
                    "required": ["param"],
                },
                execute=self._execute_do_thing,
            ),
        ]

    async def _execute_do_thing(self, param: str) -> str:
        result = await self.do_thing(param)
        import json
        return json.dumps(result, indent=2)
```