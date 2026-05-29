# Skill Bundles Standard

`jvagent` skill bundles are Claude-compatible modular capabilities consumed by the Executive's Skills center.
Each skill combines instruction content (`SKILL.md`) with optional executable tool modules and support assets.

## Canonical Structure

Every skill bundle should follow this structure:

```text
<skill_name>/
  SKILL.md              # Required entry point (frontmatter + SOP)
  scripts/              # Optional executable Python tool modules and helpers
  resources/            # Optional references, schemas, policy docs, requirements
  templates/            # Optional output templates (md/json/j2/etc)
  examples/             # Optional input/output examples
```

`SKILL.md` is required. All other directories are optional and should be created only when needed.

## SKILL.md Anatomy

`SKILL.md` has two parts:

1. YAML frontmatter metadata (between `---` delimiters)
2. Markdown SOP body (`Workflow`, `Scope`, `Grounding`, constraints, etc.)

Example:

```markdown
---
name: code_review
description: Review code for correctness, security, and maintainability.
allowed-tools:
  - prioritize_findings
version: 1
tags:
  - quality
  - security
---

## Workflow
1. Collect context.
2. Evaluate risk.
3. Produce findings and next actions.
```

## Frontmatter Keys

| Key | Required | Type | jvagent extension | Notes |
|-----|----------|------|-------------------|-------|
| `name` | recommended | `str` | no | Defaults to folder name when omitted (warning emitted). |
| `description` | recommended | `str` | no | Used in the skill index shown before activation. |
| `version` | optional | `int` or `str` | no | Version tracking metadata. |
| `license` | optional | `str` | no | Optional license metadata. |
| `tags` | optional | `list[str]` or `str` | no | Used for `scope_hint` generation and discovery cues. |
| `plan-steps` | optional | `list[str]` or `str` | yes | Suggested canonical task-tracker steps surfaced after `read_skill` to reduce planning overhead. |
| `requires-actions` | optional | `list[str]` or `str` | yes | Action types that must resolve before skill activation. |
| `requires-jvagent` | optional | `str` | yes | Framework version constraint (same operators as semver-like deps, e.g. `>=0.0.1`). Checked at preflight. |
| `requires-action-versions` | optional | `dict` | yes | Maps `namespace/label` package refs to version constraints (uses each action package ``metadata.version`` from ``info.yaml``). |
| `allowed-tools` | optional | `list[str]` or `str` | yes | Tool whitelist by tool name. |
| `response-mode` | optional | `str` | yes | `respond` or `publish`; if omitted, inherits action default. |

## Tool Module Contract (`scripts/`)

Each non-private `.py` file in `scripts/` (except `__init__.py` and `_`-prefixed files) is a candidate tool.
Tool modules must export:

1. `get_tool_definition() -> dict`
2. `async def execute(...)`

Standalone tool pattern:

```python
from typing import Any, Dict, List

def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "prioritize_findings",
        "description": "Sort findings by severity in descending order.",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {"type": "array"},
            },
            "required": ["findings"],
        },
    }

async def execute(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = list(arguments.get("findings") or [])
    findings.sort(key=lambda item: int(item.get("severity", 0)), reverse=True)
    return findings
```

Action-bound tool pattern:

```python
from typing import Any, Dict

async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleCalendarAction")
    if action is None:
        return {"error": "GoogleCalendarAction not found on this agent"}

    return await action.create_event(...)
```

Rules:

- `visitor` is injected by `ToolExecutor` when detected via `inspect.signature()`.
- Guard `action_resolver` lookup and unresolved actions explicitly.
- Define tool `name` as bare name in `get_tool_definition()`. Runtime names are exposed as `<skill_name>__<tool_name>`.
- Keep helper-only modules private by prefixing filenames with `_`.

## Optional Subdirectories

- `resources/`: long-form docs, policies, schemas, dependency files, reference data.
- `templates/`: renderable templates used by tools (for example Jinja2 or markdown skeletons).
- `examples/`: canonical examples and expected outputs for few-shot behavior shaping.

## User-Scoped File I/O

For user artifacts, do not rely on host-relative file paths. Use `fileinterface` tools and/or private helpers in `jvagent.skills.fileinterface.scripts._core`.

- Relative paths resolve under `<sanitized_agent_id>/<sanitized_user_id>/` in jvspatial storage.
- LLM-facing workflows should call `fileinterface__describe_write_workspace` before other fileinterface operations for a new write task.
- Process-local temp files (for compilers/subprocesses) are allowed when they are ephemeral.

## Cross-Skill Imports

When importing helpers across skills, use explicit package paths:

```python
from jvagent.skills.fileinterface.scripts._core import copy_host_file_into_sandbox
from jvagent.skills.pdf_generation.scripts._document_args import parse_document_pdf_arguments
```

Avoid relative imports that depend on runtime working directory.

## Discovery and Activation Lifecycle

Skills are lazily activated through progressive disclosure:

1. The Skills center resolves skill bundles from configured sources.
2. Metadata is registered, but tool modules are hidden initially.
3. LLM sees only `read_skill` and the skill index.
4. LLM calls `read_skill(skill_name=...)`.
5. The Skills center loads tools from that bundle and returns the SOP.
6. Newly activated tools become available on the next loop iteration.

This mirrors the Claude skill model: discover first, activate only when needed.

## Skill Sources and Precedence

Sources:

1. Built-in: `jvagent/skills/*`
2. App-local: `agents/<namespace>/<agent_id>/skills/*`

Precedence:

```text
App-local skill > built-in skill (same name => app-local overrides)
```

## Per-Agent Configuration

Configure from `agent.yaml`:

```yaml
- action: jvagent/skills_center
  context:
    skills: -all
    denied_skills:
      - triage
    skills_source: both   # builtin | app | both | none
```

| Selector | Behavior |
|----------|----------|
| `skills: -all` | Expose all resolved bundles |
| `skills: ["name", "glob*"]` | Expose only matching bundles |
| `skills: null` or omitted | Expose no bundles |

| `skills_source` | Resolution scope |
|-----------------|------------------|
| `both` (default) | Built-in + app-local |
| `builtin` | Built-in only |
| `app` | App-local only |
| `none` | Disable resolution |

## CLI Workflow

Create:

```bash
jvagent skill add <agent_ref> <skill_name> [--description TEXT] [--force]
```

List:

```bash
jvagent skill list [--agent <agent_ref>] [--builtin]
```

Show:

```bash
jvagent skill show <skill_name> [--agent <agent_ref>] [--builtin]
```

## Building New Skills

Built-in:

1. Create `jvagent/skills/<skill_name>/`.
2. Add `SKILL.md` with frontmatter + SOP.
3. Add tool modules to `scripts/` when needed.
4. Add optional `resources/`, `templates/`, `examples/` as needed.
5. Use `allowed-tools` to whitelist tool exposure when needed.

App-local:

1. Create `agents/<ns>/<agent_id>/skills/<skill_name>/`.
2. Add `SKILL.md` (and `scripts/` if tooling is needed).
3. Enable via `skills` selector in `agent.yaml`.

Override built-in:

Create an app-local bundle with the same `name` frontmatter value as the built-in skill.

## Resolver API

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
```

## See Also

- [Executive + Centers](../../docs/EXECUTIVE.md)
- [`MCPAction` README](../action/mcp/README.md)
- `fileinterface` bundle in `jvagent/skills/fileinterface/`