# Skills Standard — two specs

A `jvagent` skill is a folder with a `SKILL.md` (YAML frontmatter + a Markdown
SOP body) that the **Orchestrator** ([`docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md))
discovers and activates through progressive disclosure. The orchestrator
manages exactly **two skill specs** — no third variation:

| Spec | What it is | How it executes |
|------|------------|-----------------|
| **JV skill** (`spec: jv`, default) | An SOP that **references tools already on the orchestrator surface** — tools furnished by Actions and InteractActions. May declare jvagent dependencies (`requires-actions`, `allowed-tools`). | On activation, `use_skill` returns the SOP body and surfaces the skill's `allowed-tools` into the callable set. The skill "executes" by coordinating those action/IA tools. |
| **Claude skill** (`spec: claude`) | A standard [Anthropic Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) folder — instructions plus bundled scripts/resources. Drop-in compatible with the agentskills.io standard. | On activation the folder is **staged into the caller's per-user sandbox**; the model reads bundled files and runs bundled scripts via the **`code_execution__bash`** tool (the [`jvagent/code_execution`](../action/code_execution) substrate). |

> Actions remain first-class tools (ADR-0012): a capability that any user calls
> directly belongs in an **Action**'s `get_tools()`, not a skill. A skill adds
> *judgment* (a JV SOP over existing tools) or *portable bundled capability*
> (a Claude skill whose scripts run in the sandbox). If you find yourself
> wrapping an action's operation in a skill, expose it on the action instead.

## SKILL.md anatomy

Two parts: YAML frontmatter (between `---`) and a Markdown SOP body.

```markdown
---
name: research                 # lowercase, numbers, hyphens (Claude rule)
description: >-
  Investigate a topic with evidence-first synthesis and citations. Include
  what it does AND when to use it (third person — it's injected into the prompt).
spec: jv                       # jv (default) | claude
allowed-tools:
  - web_search__search
  - web_fetch__fetch
metadata:
  version: 2
  tags: [research]
---

## Workflow
1. Clarify the question and success criteria.
2. Search, then read top sources in full with web_fetch__fetch.
3. Reconcile conflicts and synthesize a cited answer.
```

## Frontmatter keys

| Key | Specs | Notes |
|-----|-------|-------|
| `name` | both | Lowercase letters, numbers, hyphens (Claude rule); defaults to folder name. |
| `description` | both | Drives discovery; third person; what it does + when to use it. |
| `spec` | both | `jv` (default) or `claude`. Unknown values fall back to `jv`. |
| `allowed-tools` | mostly JV | Runtime tool names the SOP uses (e.g. `gmail__send_email`, `web_fetch__fetch`, `code_execution__bash`). Surfaced into the visible set on activation. |
| `requires-actions` | JV | Action types that must resolve before activation (hard gate). |
| `requires-jvagent` | JV | Framework version constraint, checked at preflight. |
| `requires-action-versions` | JV | `namespace/label` → version constraint. |
| `license`, `metadata` | both | Claude-standard fields. `metadata.version` / `metadata.tags` for tracking + discovery cues. |

(jvagent also parses chaining/dispatch extensions — `exports`, `imports`,
`coactivate-with`, `dispatch`, `verbatim-final`, `always-active` — for the JV
orchestration features the Claude standard doesn't cover.)

**`always-active: true`** keeps a skill's `allowed-tools` **pinned into the
visible tool set every turn** under the orchestrator — even when lean surfacing
(ADR-0018) would otherwise hide them behind `find_tool`. Use it for a capability
that must be callable turn-1 regardless of how the user phrases things, without
disabling lean for the rest of the surface. (The raw-tool equivalent is the
orchestrator's `pinned_tools` glob list.) It also lets the skill bypass the
`skills:` allow-list selector.

## JV skills — coordinate existing tools

A JV skill is pure judgment: a `SKILL.md` whose body steers tools that Actions
and InteractActions already expose. It carries **no executable code**. Examples:
`research`, `answer`. Reference the tools you use in
`allowed-tools` (and `requires-actions` if a tool *must* be present), then
describe the procedure in the body. Activation surfaces those tools so the model
can call them on the next loop tick.

## Claude skills — bundled scripts in a sandbox

A Claude skill is a standard Anthropic folder. Set `spec: claude` and bundle
whatever the procedure needs:

```text
<skill_name>/
  SKILL.md            # frontmatter + instructions (how to run the scripts)
  scripts/            # plain CLI scripts the model runs via code_execution__bash
  resources/          # reference docs/data read on demand (level-3 disclosure)
```

Scripts are **ordinary executables** (not a tool protocol) — they read args/stdin,
do work, and write output/stdout, e.g.:

```bash
python staged_skills/<skill>/scripts/render_pdf.py --input doc.md --output output/report.pdf
```

On activation the orchestrator stages the folder at `staged_skills/<name>/` inside the
caller's per-user sandbox and tells the model where to run it. Anything a script
writes lands in that user's slice and is visible to the file tools. See
[`pdf_generation`](pdf_generation) and [`triage`](triage) for working examples.

**Requires the code-execution substrate.** Claude skills only execute when
[`jvagent/code_execution`](../action/code_execution) is installed and **enabled**
on the agent (it is **off by default**). Without it, a Claude skill still
activates (its SOP loads) but its scripts cannot run.

### The multitenant sandbox

`code_execution` runs `bash` with its working directory set to the caller's own
`<agent_id>/<user_id>/` slice — the same per-user filesystem convention the
file-IO MCPs and the `file_interface` action use, centralized in
[`jvagent.core.sandbox`](../core/sandbox.py). Each user's code is walled off from
every other user's. Per-execution OS limits (no network, CPU/memory/time/output
caps, scrubbed env) come from a **pluggable executor** — a subprocess default,
swappable for a container/jail backend. The subprocess default is **not a hard
security boundary**: run only trusted skills under it, or supply an isolating
backend for untrusted/third-party skills. See the action and executor module
docstrings for the full posture.

## Discovery and activation lifecycle

1. The orchestrator resolves skill bundles from the configured sources; each
   skill's `name` + `description` are listed in the prompt (Claude level 1).
2. `find_skill` searches the index; `use_skill(name=...)` activates one.
3. Activation returns the SOP body as an observation (level 2) and:
   - **JV skill** → surfaces the skill's `allowed-tools` into the visible set.
   - **Claude skill** → stages the folder into the per-user sandbox and notes
     where to run it; the model then reads files / runs scripts via
     `code_execution__bash` (level 3) as needed.
4. `use_skill` is idempotent per turn.

## Sources, precedence, configuration

1. Built-in: `jvagent/skills/*`  2. App-local: `agents/<ns>/<agent_id>/skills/*`
(app-local overrides a built-in of the same `name`).

```yaml
- action: jvagent/orchestrator
  context:
    skills_source: both        # app | library | both
    skills: "-all"             # or a finite list: [research, pdf-generation]
    denied_skills: [triage]
# Enable Claude-skill execution (off by default):
- action: jvagent/code_execution
  context:
    enabled: true
    timeout: 60
    memory_mb: 2048
```

| `skills` selector | Behavior |
|----------|----------|
| `-all` | expose all resolved bundles |
| `["name", "glob*"]` | expose only matching |
| `null` / omitted | expose none |

## Building a new skill

**JV skill:** create `jvagent/skills/<name>/SKILL.md` with `spec: jv` (or omit),
reference the action/IA tools in `allowed-tools`, write the SOP. No code.

**Claude skill:** create `jvagent/skills/<name>/` with `spec: claude`, add
`scripts/` (plain CLI scripts) and any `resources/`, and write a SKILL.md that
tells the model how to run them via `code_execution__bash`. Declare runtime
dependencies in `resources/requirements.txt` — the sandbox has no network, so
they must be present in the host image.

## Resolver API

```python
from jvagent.scaffold.skill_resolve import (
    parse_skill_bundle, resolve_builtin_skills, resolve_agent_skills,
    resolve_merged_skill_bundles, apply_skill_selector,
    list_builtin_skill_names, list_agent_skill_names,
)
```

## See also

- [Orchestrator](../../docs/ORCHESTRATOR.md) — the loop and skill lifecycle
- [`jvagent/code_execution`](../action/code_execution) — the sandbox substrate
- [`jvagent/core/sandbox.py`](../core/sandbox.py) — the per-user FS convention
- [`MCPAction` README](../action/mcp/README.md) — external tool servers
