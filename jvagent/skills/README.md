# Skill Bundles

Claude-style skill bundles for `SkillInteractAction`. Each bundle is a directory containing a `SKILL.md` SOP file and optional Python tool modules.

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
| `requires-actions` | Optional | list[str] or str | Action entity types this skill depends on (e.g. `GoogleCalendarAction`). If any are missing or disabled at activation time, `read_skill` returns an error. |
| `response-mode` | Optional | str | Override the action's response mode for this skill: `respond` (route through PersonaAction) or `publish` (direct bus delivery). If omitted, inherits the action's `response_mode` attribute. |
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

### User-scoped file I/O (required for user artifacts)

Skills that read or write **user-owned files** (outputs, uploads, drafts, pipeline artifacts) must **not** use the host filesystem for relative paths. Use the **`fileinterface`** skill and/or `jvagent.skills.fileinterface._core`:

- Paths are relative to `<sanitized_agent_id>/<sanitized_user_id>/` in jvspatial storage (local or S3 per app config).
- LLM-facing tools: activate the `fileinterface` bundle and follow its SKILL protocol (`describe_write_workspace` before other fileinterface tools when starting file work in a task); then use `fileinterface__read_file`, `fileinterface__write_file`, etc.
- Imperative Python in tool modules: import `_core` (e.g. `write_text_file`, `read_binary_file`, or `*_with_local_fallback` when App may be absent).

**Fine to use raw files for:** process-local **temporary** directories (subprocesses, compilers), **absolute** paths to known app/corpus locations, **URLs**, and bundles that **manage the repo** (e.g. `skill_hub` under `agents/...`).

### Action-Bound Tools

Tool modules that need to call methods on graph-persisted Actions (like `GoogleCalendarAction.list_events()`) should:

1. Declare the required actions in `SKILL.md` frontmatter:

   ```yaml
   requires-actions:
     - GoogleCalendarAction
   ```

2. Accept a `visitor` keyword argument in `execute()`:

   ```python
   async def execute(arguments: dict, *, visitor: Any) -> Any:
       action = await visitor.action_resolver.resolve("GoogleCalendarAction")
       if action is None:
           return {"error": "GoogleCalendarAction not available"}
       return await action.list_events()
   ```

The `visitor` kwarg is automatically injected by ToolExecutor when it detects it in the function signature via `inspect.signature()`. The `visitor.action_resolver` attribute is set by SkillInteractAction at loop startup and provides per-interaction caching of resolved Actions.

## Sources

Skill bundles are resolved from two locations:

### 1. Built-in Catalog (`jvagent/skills/*`)

```text
jvagent/skills/
  calendar/
    SKILL.md
    list_events.py
    create_event.py
    delete_event.py
  gmail/
    SKILL.md
    send_email.py
    list_messages.py
    get_message.py
    mark_read.py
    get_profile.py
  google_sheets/
    SKILL.md
    read_spreadsheet.py
    ...
  google_drive/
    SKILL.md
    upload_file.py
    ...
  outlook_calendar/
    SKILL.md
    ...
  outlook_mail/
    SKILL.md
    ...
  microsoft_excel/
    SKILL.md
    ...
  microsoft_onedrive/
    SKILL.md
    ...
  web_search/
    SKILL.md
    search.py
  fileinterface/
    SKILL.md
    read_file.py
    write_file.py
    ...
  pageindex_search/
    SKILL.md
    search.py
  pageindex_docs/
    SKILL.md
    list_documents.py
    assimilate.py
    delete_document.py
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
agents/jvagent/skills_agent/
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

1. `SkillInteractAction` resolves bundles from the configured source.
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

In `agent.yaml`, control which bundles are exposed via `SkillInteractAction`:

```yaml
- action: jvagent/skill_interact_action
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

| Skill | Description | Tools | Requires Actions |
|-------|-------------|-------|-----------------|
| `calendar` | Manage Google Calendar events (list, create, delete) | `list_events`, `create_event`, `delete_event` | `GoogleCalendarAction` |
| `gmail` | Send and manage Gmail messages | `send_email`, `list_messages`, `get_message`, `mark_read`, `get_profile` | `GoogleGmailAction` |
| `google_sheets` | Read, write, and manage Google Sheets | `read_spreadsheet`, `last_filled_row`, `update_spreadsheet`, `append_spreadsheet`, `batch_clear`, `format_cells`, `merge_cells`, `unmerge_cells`, `create_spreadsheet`, `create_worksheet`, `update_worksheet`, `delete_worksheet`, `share_spreadsheet`, `delete_spreadsheet` | `GoogleSheetsAction` |
| `google_drive` | Upload, share, and manage Google Drive files | `upload_file`, `delete_file`, `get_file_metadata`, `list_files`, `share_file`, `get_media` | `GoogleDriveAction` |
| `outlook_calendar` | Manage Outlook Calendar events (list, create, delete) | `list_events`, `create_event`, `delete_event` | `MicrosoftOutlookCalendarAction` |
| `outlook_mail` | Send and manage Outlook mail messages | `send_email`, `list_messages`, `list_inbox_messages`, `get_message`, `mark_read`, `get_profile` | `MicrosoftOutlookMailAction` |
| `microsoft_excel` | Read, write, and manage Excel workbooks | `read_spreadsheet`, `update_spreadsheet`, `append_spreadsheet`, `batch_clear`, `create_spreadsheet`, `create_worksheet`, `update_worksheet`, `delete_worksheet`, `share_spreadsheet`, `delete_spreadsheet` | `MicrosoftExcelAction` |
| `microsoft_onedrive` | Upload, share, and manage OneDrive files | `upload_file`, `delete_file`, `list_files`, `share_file` | `MicrosoftOneDriveAction` |
| `web_search` | Search the web for current information | `search` | `SerperWebSearchAction` |
| `pageindex_search` | Search PageIndex documents using vectorless retrieval | `search` | `PageIndexAction` |
| `pageindex_docs` | List, ingest, and remove PageIndex documents | `list_documents`, `assimilate`, `delete_document` | `PageIndexAction` |
| `code_review` | Review code for correctness, security, and maintainability | (SOP only) | — |
| `research` | Investigate a topic with evidence-first synthesis and citations | (SOP only) | — |
| `triage` | Rapidly triage issues by severity, impact, and next action | `prioritize_findings` | — |

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
app_local = resolve_agent_skills(app_root=".", namespace="jvagent", agent_name="skills_agent")

# Merge with precedence
merged = resolve_merged_skill_bundles(".", "jvagent", "skills_agent", include_builtin=True)

# Apply selector and deny filters
filtered = apply_skill_selector(merged, selector="-all", denied=["triage"])
filtered = apply_skill_selector(merged, selector=["code_*"], denied=None)
```

## See Also

- [SkillInteractAction README](../action/skill/README.md) -- The agentic loop that consumes skill bundles
- **`fileinterface` skill** (`fileinterface/`) -- In-process user-scoped file I/O (jvspatial local/S3); prefer over MCP for workspace files only
- [MCPAction README](../action/mcp/README.md) -- MCP server configuration for external or multi-server tool providers

---

**Last Updated**: April 19, 2026
**Version**: 0.0.1