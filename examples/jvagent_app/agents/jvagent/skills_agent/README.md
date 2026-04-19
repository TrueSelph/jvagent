# Skills Agent

A skill-driven agent demonstrating jvagent's agentic loop capabilities.

## Overview

This agent uses **SkillInteractAction** to implement a skill-driven think-act-observe loop. The LLM reasons about tasks, calls tools via MCP servers, observes results, and iterates until the task is complete.

## Architecture

```
User utterance
    │
    ▼
InteractRouter (classifies intent)
    │
    ├── Simple question ──► PersonaAction (direct LLM response)
    │
    └── Multi-step task ──► SkillInteractAction
                              │
                              ▼
                        Initialize ToolExecutor
                        (registers MCP tools as LLM-callable functions)
                              │
                              ▼
                        Agentic Loop:
                          1. Call LLM with tools
                          2. If tool_calls → dispatch via ToolExecutor
                          3. Append results to conversation
                          4. Repeat until done
                              │
                              ▼
                        Final response published
```

## Components

| Component | Archetype | Role |
|-----------|-----------|------|
| InteractRouter | `InteractRouter` | Intent classification and routing |
| OpenAI LM | `OpenAILanguageModelAction` | LLM for routing and conversational responses |
| Filesystem MCP | `MCPAction` | Exposes filesystem tools (read, write, search) |
| Skills Catalog | `jvagent/skills/*` + `agents/<ns>/<id>/skills/*` | Reusable SKILL.md bundles and optional tool modules |
| Skills Agent | `SkillInteractAction` | Agentic loop: think → act → observe → repeat |
| Persona | `PersonaAction` | Simple conversational responses |
| Converse | `ConverseInteractAction` | Smalltalk fallback |

## Setup

### 1. Environment Variables

Create a `.env` file in your app root:

```bash
# Required for OpenAI (routing + conversational responses)
OPENAI_API_KEY=sk-...

# Optional: filesystem MCP root (defaults to /workspace)
MCP_FILESYSTEM_ROOT=/path/to/your/project
```

### 2. Register the Agent

Add `jvagent/skills_agent` to your `app.yaml` agents list:

```yaml
agents:
  - jvagent/skills_agent
```

### 3. MCP Server

The filesystem MCP server requires Node.js and npx. It starts automatically via stdio transport when the agent is initialized.

## Usage Examples

### Code Review
```
"Review the authentication module for security issues"
```
The skills agent will:
1. List directory to find relevant files
2. Read authentication source files
3. Analyze for security vulnerabilities
4. Provide structured feedback

### Multi-step Analysis
```
"Find all API endpoints and check if they have proper error handling"
```
The skills agent will:
1. Search for route/endpoint definitions
2. Read each endpoint handler
3. Check error handling patterns
4. Summarize findings

### Simple Questions (routed to Persona)
```
"What is REST?"
```
The InteractRouter routes this to PersonaAction for a direct response—no tools needed.

## Configuration

### Adjusting Loop Limits

In `agent.yaml`, under the `skill_interact_action` context:

```yaml
max_iterations: 25          # Max think-act-observe cycles
max_duration_seconds: 300   # Wall-clock timeout (seconds)
thinking_budget_tokens: 10000  # Anthropic extended thinking budget
```

### Adding MCP Servers

Add new MCP actions and include them in `tool_servers`:

```yaml
- action: jvagent/mcp
  context:
    enabled: true
    server_name: "websearch"
    transport: "streamable_http"
    url: "http://localhost:3001/mcp"

- action: jvagent/skill_interact_action
  context:
    tool_servers:
      - "filesystem"
      - "websearch"
```

### Creating and Selecting Skills

Skills are SKILL.md bundles, not action entries:

```text
agents/<namespace>/<agent_id>/skills/<skill_name>/SKILL.md
```

This example agent includes an app-local bundle at:

```text
agents/jvagent/skills_agent/skills/local_research/SKILL.md
```

Select which bundles are exposed in the skill interact action:

```yaml
- action: jvagent/skill_interact_action
  context:
    skills: ["code_review", "local_research"]
    denied_skills: ["triage"]
    skills_source: both   # builtin | app | both | none
```

For unrestricted skill exposure, use:

```yaml
skills: -all
```