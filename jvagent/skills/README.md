# Skill Bundles Standard

`jvagent` skills are Claude-compatible modular augmentations consumed by the
**Orchestrator** ([`docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md)). A skill pairs
instruction content (`SKILL.md`) with an optional set of executable tool modules
and support assets.

## Skills vs. actions — read this first

Under the Orchestrator pattern (ADR-0012), **actions are first-class tools**:
an `Action` only needs to implement `get_tools()` and its capabilities are
directly callable by the orchestrator. A **skill is an augmentation**, not a
capability host. Use a skill to:

1. **Guide and coordinate** — an SOP that references one or more existing tools
   (action tools, core tools) and steers their use toward an outcome
   (`research`, `answer`, `code_review`, `triage`). These carry little or no
   code; they reference tools by name in `allowed-tools` and the SOP body.
2. **Provide genuinely new capability** — bundle tool modules in `scripts/` only
   when the capability is **not** offered by any action (`pdf_generation`'s LaTeX
   rendering, `fileinterface`'s sandboxed I/O, `skill_hub`'s registry ops).

**Do not** write a skill whose `scripts/` re-wrap an action's operations. That
duplication is exactly what this library was cleaned of: if an action already
exposes the operation via `get_tools()`, reference that tool from a skill SOP —
or just let the executive call the action tool directly. Need a new operation on
an existing integration? Add it to that action's `get_tools()`, not a skill stub.

## Canonical Structure

```text
<skill_name>/
  SKILL.md              # Required entry point (frontmatter + SOP)
  scripts/              # Optional — ONLY for new-capability tool modules
  resources/            # Optional references, schemas, policy docs, requirements
  templates/            # Optional output templates (md/json/j2/etc)
  examples/             # Optional input/output examples
```

`SKILL.md` is required. All other directories are optional and created only when
needed. A guidance-only skill is just a `SKILL.md`.

## SKILL.md Anatomy

`SKILL.md` has two parts: YAML frontmatter metadata (between `---` delimiters)
and a Markdown SOP body (`Workflow`, `Scope`, `Grounding`, constraints, etc.).

```markdown
---
name: research
description: Investigate a topic with evidence-first synthesis and citations.
allowed-tools:
  - web_search__search
  - web_fetch__fetch
version: 2
tags:
  - research
---

## Workflow
1. Clarify the question and success criteria.
2. Search, then read top sources in full with web_fetch__fetch.
3. Reconcile conflicts and synthesize a cited answer.
```

## Frontmatter Keys

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `name` | recommended | `str` | Defaults to folder name when omitted (warning emitted). |
| `description` | recommended | `str` | Used in the skill index shown before activation. |
| `version` | optional | `int`/`str` | Version tracking metadata. |
| `tags` | optional | `list[str]` | Discovery cues / `scope_hint` generation. |
| `requires-actions` | optional | `list[str]` | Action types that must resolve before activation (e.g. a guidance skill that needs `PageIndexAction`'s tools present). |
| `requires-jvagent` | optional | `str` | Framework version constraint, checked at preflight. |
| `allowed-tools` | optional | `list[str]` | Tools this skill uses — **reference real tool names** (action tools like `gmail__send_email`, core tools, or this skill's own `scripts/` tools). Surfaced into the visible set on activation. |

## Tool Module Contract (`scripts/`)

Only for **new-capability** skills. Each non-private `.py` in `scripts/` (except
`__init__.py` and `_`-prefixed helpers) is a candidate tool exporting:

1. `get_tool_definition() -> dict` (bare `name`; runtime name is `<skill>__<name>`)
2. `async def execute(...)`

```python
from typing import Any, Dict, List

def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "prioritize_findings",
        "description": "Sort findings by severity (descending).",
        "parameters": {
            "type": "object",
            "properties": {"findings": {"type": "array", "items": {}}},
            "required": ["findings"],
        },
    }

async def execute(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = list(arguments.get("findings") or [])
    findings.sort(key=lambda i: int(i.get("severity", 0)), reverse=True)
    return findings
```

Rules:

- Keep helper-only modules private by prefixing filenames with `_`.
- A `scripts/` tool should deliver capability the skill owns — not delegate to an
  action method. If you find yourself resolving an action inside a skill tool to
  call one of its methods, expose that operation on the action's `get_tools()`
  instead and reference it from the SOP.

## Optional Subdirectories

- `resources/`: long-form docs, policies, schemas, dependency files, reference data.
- `templates/`: renderable templates used by tools (e.g. Jinja2 or markdown skeletons).
- `examples/`: canonical examples and expected outputs for few-shot shaping.

## User-Scoped File I/O

For user artifacts, do not rely on host-relative paths. Use `fileinterface`
tools and/or private helpers in `jvagent.skills.fileinterface.scripts._core`.

- Relative paths resolve under `<sanitized_agent_id>/<sanitized_user_id>/` in jvspatial storage.
- Call `fileinterface__describe_write_workspace` before other fileinterface operations for a new write task.
- Process-local temp files (for compilers/subprocesses) are allowed when ephemeral.

## Cross-Skill Imports

Use explicit package paths; avoid relative imports that depend on cwd:

```python
from jvagent.skills.fileinterface.scripts._core import copy_host_file_into_sandbox
from jvagent.skills.pdf_generation.scripts._document_args import parse_document_pdf_arguments
```

## Discovery and Activation Lifecycle

Skills are lazily activated through progressive disclosure by the Orchestrator:

1. The executive resolves skill bundles from the configured sources.
2. Metadata is registered, but a skill's tools stay hidden initially.
3. The model sees `find_skill` / `use_skill` plus the skill index.
4. The model calls `use_skill(name=...)`.
5. The executive returns the SOP body as an observation and surfaces the skill's
   `allowed-tools` into the visible tool set.
6. Those tools are callable on the next loop tick.

This mirrors the Claude skill model: discover first, activate only when needed.

## Skill Sources and Precedence

1. Built-in: `jvagent/skills/*`
2. App-local: `agents/<namespace>/<agent_id>/skills/*`

App-local overrides a built-in skill of the same `name`.

## Per-Agent Configuration

Configure on the Orchestrator action in `agent.yaml`:

```yaml
- action: jvagent/orchestrator
  context:
    skills_source: both        # app | library | both
    skills: "-all"             # or a finite list: [research, answer]
    denied_skills:
      - triage
```

| Selector | Behavior |
|----------|----------|
| `skills: -all` | Expose all resolved bundles |
| `skills: ["name", "glob*"]` | Expose only matching bundles |
| `skills: null` / omitted | Expose no bundles |

| `skills_source` | Resolution scope |
|-----------------|------------------|
| `both` (default) | Library + app-local |
| `library` (alias `builtin`) | Library only |
| `app` (alias `local`) | App-local only |

## Building New Skills

Built-in:

1. Create `jvagent/skills/<skill_name>/` with `SKILL.md` (frontmatter + SOP).
2. Reference the tools the SOP uses in `allowed-tools`.
3. Add `scripts/` **only** for genuinely new capability tools (not action stubs).
4. Add `resources/`, `templates/`, `examples/` as needed.

App-local: create `agents/<ns>/<agent_id>/skills/<skill_name>/` and enable via
the `skills` selector. Use the same `name` to override a built-in.

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

- [Orchestrator](../../docs/ORCHESTRATOR.md) — the orchestrator and its skill lifecycle
- [`MCPAction` README](../action/mcp/README.md) — external tool servers
- `fileinterface`, `pdf_generation`, `skill_hub` bundles for new-capability examples
