# Skill Bundles

Claude-style skill bundles for `ThinkingInteractAction`. Each bundle is a directory containing a `SKILL.md` SOP file and optional Python tool modules.

## Layout

Each bundle follows this structure:

```text
<skill_name>/
  SKILL.md          # Required: SOP with optional YAML frontmatter
  <tool>.py         # Optional: Python tool modules
  __init__.py       # Optional: makes the directory a Python package
```

### SKILL.md Format

```markdown
---
name: code_review
description: Review code for correctness, security, and maintainability.
allowed-tools:
  - my_tool
version: 1
tags:
  - quality
  - security
---

## Workflow

1. First step of the standard operating procedure.
2. Second step.
3. ...
```

### Frontmatter Keys

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `name` | Recommended | str | Skill identifier. Defaults to directory name if omitted. |
| `description` | Recommended | str | Shown in the skill index that the LLM sees at loop start. |
| `allowed-tools` | Optional | list[str] or str | Whitelist of Python tool names to activate from this bundle. If omitted, all `.py` tools are activated. |
| `version` | Optional | int/str | Version number for tracking |
| `license` | Optional | str | License identifier |
| `tags` | Optional | list[str] | Tags for categorization |

### Tool Modules

Each `.py` file (excluding `__init__.py` and `_`-prefixed files) in the skill directory is a potential tool. It must export two functions:

```python
def get_tool_definition() -> dict:
    """Return an OpenAI-format tool definition."""
    return {
        "name": "prioritize_findings",
        "description": "Sort findings by severity in descending order.",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "severity": {"type": "integer"},
                        },
                        "required": ["title", "severity"],
                    },
                }
            },
            "required": ["findings"],
        },
    }


async def execute(arguments: dict) -> Any:
    """Implement the tool logic."""
    findings = list(arguments.get("findings") or [])
    findings.sort(key=lambda item: int(item.get("severity", 0)), reverse=True)
    return findings
```

If `allowed-tools` is set in the frontmatter, only tools whose names appear in the whitelist are activated. Tools not in the whitelist are silently skipped.

## Sources

Skill bundles are resolved from two locations:

### 1. Built-in Catalog (`jvagent/skills/*`)

```text
jvagent/skills/
  code_review/
    SKILL.md
  research/
    SKILL.md
  triage/
    SKILL.md
    prioritize_findings.py
```

These ship with jvagent and are available to all agents.

### 2. App-Local (`agents/<namespace>/<agent_id>/skills/*`)

```text
agents/jvagent/thinking_agent/
  skills/
    local_research/
      SKILL.md
    custom_analysis/
      SKILL.md
      analyze_data.py
```

These are per-agent custom bundles. When an app-local bundle has the same `name` as a built-in, the app-local bundle **overrides** the built-in.

## Resolution Precedence

```
App-local skill  >  Built-in skill  (same name = app-local wins)
```

This lets you customize or replace any built-in skill without modifying the jvagent package.

## Progressive Disclosure

Skills are **not** eagerly loaded. The flow is:

1. `ThinkingInteractAction` resolves bundles from the configured source.
2. Bundle metadata is registered on `ToolExecutor`, but Python tools are **hidden** from the LLM.
3. A `read_skill` tool is injected, along with a skill index in the system prompt:

```
You have access to the following Claude-style skill bundles.
If the user's request aligns with one of them, call `read_skill` with the
exact `skill_name` before attempting the specialized workflow.
Skill tools are only exposed after `read_skill`.

- code_review: Review code for correctness, security, and maintainability.
- triage: Rapidly triage issues by severity, impact, and next action.
```

4. When the LLM calls `read_skill(skill_name="triage")`:
   - `ToolExecutor.activate_skill("triage")` loads the `prioritize_findings.py` module
   - The handler returns the full SOP content and a list of newly available tools
   - On the next loop iteration, `get_tools_list()` includes the newly activated tool

This mirrors the Claude Code skill model: the LLM discovers capabilities on demand rather than seeing every tool upfront.

## Per-Agent Configuration

In `agent.yaml`, control which bundles are exposed via `ThinkingInteractAction`:

```yaml
- action: jvagent/thinking_interact_action
  context:
    # Skill bundle selector
    skills: -all                # Expose all discovered bundles
    # skills:                    # Omit -> no bundles exposed (default)
    # skills:
    #   - "code_review"          # Specific names
    #   - "code_*"               # Glob patterns

    # Subtractive filter
    denied_skills:
      - "triage"                # Remove from resolved set

    # Source control
    skills_source: both         # builtin | app | both | none
```

| Selector | Behavior |
|----------|----------|
| `skills: -all` | Expose all discovered bundles |
| `skills: ["name", "glob*"]` | Expose only matching bundles |
| `skills: null` / omitted | No bundles exposed (opt-in default) |

| `skills_source` | Bundles resolved from |
|------------------|-----------------------|
| `both` (default) | Built-in + app-local (app overrides built-in) |
| `builtin` | `jvagent/skills/*` only |
| `app` | `agents/<ns>/<id>/skills/*` only |
| `none` | No resolution |

## CLI Commands

### Create a Skill Bundle

```bash
jvagent skill add <agent_ref> <skill_name> [--description TEXT] [--force]
```

Creates a `SKILL.md` skeleton under `agents/<ns>/<id>/skills/<skill_name>/`.

### List Bundles

```bash
jvagent skill list [--agent <agent_ref>] [--builtin]
```

- `--agent`: Show merged bundles (built-in + app-local) for a specific agent
- `--builtin`: Show only built-in bundles

### Show Bundle Details

```bash
jvagent skill show <skill_name> [--agent <agent_ref>] [--builtin]
```

Displays the full SKILL.md content and metadata.

## Built-in Skills

| Skill | Description | Tools |
|-------|-------------|-------|
| `code_review` | Review code for correctness, security, and maintainability | (SOP only) |
| `research` | Investigate a topic with evidence-first synthesis and citations | (SOP only) |
| `triage` | Rapidly triage issues by severity, impact, and next action | `prioritize_findings` |

## Creating a New Built-in Skill

1. Create a directory under `jvagent/skills/`:

```text
jvagent/skills/my_skill/
  SKILL.md
  my_tool.py    # optional
```

2. Write SKILL.md with frontmatter and SOP content.

3. If the skill includes tools, add `.py` files that export `get_tool_definition()` and `execute()`.

4. List the tool names in `allowed-tools` frontmatter if you want to restrict which `.py` files are activated.

## Creating an App-Local Skill

1. Create a directory under the agent's `skills/` folder:

```text
agents/jvagent/my_agent/skills/my_custom_skill/
  SKILL.md
```

2. Or use the CLI:

```bash
jvagent skill add jvagent/my_agent my_custom_skill --description "Custom skill"
```

3. Reference it from `agent.yaml`:

```yaml
skills:
  - "my_custom_skill"
```

## Overriding a Built-in Skill

Place an app-local bundle with the same `name` in the frontmatter:

```text
agents/jvagent/my_agent/skills/research/
  SKILL.md
```

```markdown
---
name: research
description: Custom research workflow for our domain.
version: 2
---
...
```

When `skills_source: both`, the app-local `research` bundle replaces the built-in one.

## Skill Resolver API

```python
from jvagent.scaffold.skill_resolve import (
    parse_skill_bundle,
    resolve_builtin_skills,
    resolve_agent_skills,
    resolve_merged_skill_bundles,
    apply_skill_selector,
    list_builtin_skill_names,
    list_agent_skill_names,
)

# Parse a single bundle
data = parse_skill_bundle(Path("jvagent/skills/triage"), source="builtin")
# {"name": "triage", "description": "...", "content": "...", "dir": "...", "tool_files": [...], ...}

# Resolve all built-in skills
builtin = resolve_builtin_skills()

# Resolve app-local skills for an agent
app_local = resolve_agent_skills(app_root=".", namespace="jvagent", agent_name="thinking_agent")

# Merge with precedence
merged = resolve_merged_skill_bundles(".", "jvagent", "thinking_agent", include_builtin=True)

# Apply selector and deny filters
filtered = apply_skill_selector(merged, selector="-all", denied=["triage"])
filtered = apply_skill_selector(merged, selector=["code_*"], denied=None)
```

## See Also

- [ThinkingInteractAction README](../action/thinking/README.md) -- The agentic loop that consumes skill bundles
- [MCPAction README](../action/mcp/README.md) -- MCP server configuration for tool providers

---

**Last Updated**: April 18, 2026
**Version**: 0.0.1