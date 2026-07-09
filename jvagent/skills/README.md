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

## Skill placement standard (ADR-0023)

**Rule:** drop every agent skill into `agents/<ns>/<agent>/skills/<name>/` unless one of the two exceptions below applies.

| You are… | Put it here | Listed as orchestrator skill? |
|----------|-------------|-------------------------------|
| **Authoring a skill for an agent** (JV SOP, interview, Claude bundle) | `agents/<ns>/<agent>/skills/<name>/` | Yes |
| **Shipping a reusable library skill in jvagent** | `jvagent/skills/<name>/` | Yes (`source: builtin`) |
| **Furnishing a base action procedure** (inherited, not activated alone) | `<action_dir>/SKILL.md` | **No** — `extends: action:…` source only |
| **Bundling a skill with a custom/core action package** | `<action_dir>/skills/<name>/` or `agents/.../actions/<ns>/<action>/skills/<name>/` | Yes — only when the skill ships **with** that action distribution |

### Decision flow

```
New skill for my agent?
  └─ YES → agents/<ns>/<agent>/skills/<name>/     ← default (always)

Bundled inside an action package I own (agents/.../actions/... or jvagent/action/...)?
  └─ YES → <that action>/skills/<name>/

Base procedure for an action (interview tool loop, etc.)?
  └─ YES → <action_dir>/SKILL.md  (not a skill folder)
```

**Interview skills** (`interview:` frontmatter, `scripts/custom_tools.py`) follow the same default: `agents/.../skills/<name>/` plus `extends: action:jvagent/interview`. Do not place them under `agents/.../actions/jvagent/interview/skills/` unless you are distributing a **custom fork** of `InterviewAction` with its own bundled skills.

All skills and actions must obey the **[thin harness principle](../../docs/thin-harness.md)** — thick SOP + skill extensions, thin server harness. Interview skills also follow the **[interview profile](../action/interview/docs/thin-harness.md)**. AI agents must not add extractors, prep observations, or foundation-side intent logic when extending interviews.

`jvagent skill add <agent_ref> <name>` scaffolds into `agents/.../skills/<name>/` by design.

### Layout example

```
jvagent/action/interview/
├── SKILL.md                    # base SOP (extends target — not discovered)
├── interview_action.py
└── examples/example_interview/ # copy template (not auto-discovered)

agents/acme/bot/skills/         # ← all agent skills live here
├── signup_interview/           # extends action:jvagent/interview
├── web_lookup/
└── docx/                       # spec: claude
```

Discovery tiers (merge order): builtin library → core action `skills/` → app `skills/` → app action overlays. See [ADR-0031](../../.planning/adr/0031-skill-sop-extends.md) for `extends` and [ADR-0023](../../.planning/adr/0023-skill-placement-standard.md) for placement.

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
| `requires-actions` | JV | Action class names that must resolve (enabled) on the agent, each with an **optional inline version constraint** (PEP 508-style — the comparison operator is the delimiter): `CodeExecutionAction`, `PageIndexAction>=2.0`, `WebFetchAction==1.4.0`, `GmailAction>=1.0,<2.0`. **Hard gate, enforced:** if any declared type is absent — or its `get_version()` doesn't satisfy the constraint — the orchestrator hides the skill entirely for that turn (not listed, found, activated, or always-active-pinned). Replaces the old `requires-action-versions` map. **Not lifecycle binding:** listing multiple actions (e.g. `InterviewAction` + `ZoonAPIAction`) gates on all of them; which Action runs `on_skill_activate` / `prepare_task_lock_turn` is resolved separately (see below). |
| `requires-jvagent` | JV | Framework version constraint, checked at preflight. |
| `extends` | JV | SOP inheritance only (body composition). `action:<namespace>/<action>` loads `<action_dir>/SKILL.md` body; `skill:<name>` inherits another skill's composed body. Separate from `requires-actions`. When `extends: action:…` is set, that action ref is also the **preferred lifecycle binder** for skill hooks (`on_skill_activate`, `prepare_task_lock_turn`, `resolve_task_lock_skill`, etc.). |
| `license`, `metadata` | both | Claude-standard fields. `metadata.version` / `metadata.tags` for tracking + discovery cues. |

(jvagent also parses chaining/dispatch extensions — `exports`, `imports`,
`coactivate-with`, `dispatch`, `verbatim-final`, `always-active`, `task-lock`,
`lock-companions` — for the JV orchestration features the Claude standard
doesn't cover.)

**`always-active: true`** keeps a skill's `allowed-tools` **pinned into the
visible tool set every turn** under the orchestrator — even when lean surfacing
(ADR-0018) would otherwise hide them behind `find_tool`. Use it for a capability
that must be callable turn-1 regardless of how the user phrases things, without
disabling lean for the rest of the surface. (The raw-tool equivalent is the
orchestrator's `pinned_tools` glob list.) It also lets the skill bypass the
`skills:` allow-list selector.

### Orchestrator `auto_start_skills_on_new_user`

List skill names on the orchestrator (`auto_start_skills_on_new_user: [my_skill]`).
For each **new user**, the orchestrator mechanically runs `use_skill` before the
first model tick (activation/bootstrap uses the skill's **lifecycle-bound** Action — see binding below).

When a `task-lock: true` skill has an **active** TaskStore task (`owner_action`
matches the skill name), the orchestrator restricts the tool surface to that skill until
the task is **completed**. Interview skills delegate session resolution to the bound
Action's `resolve_task_lock_skill()` (typically `InterviewAction` via `extends: action:jvagent/interview`).
The `use_skill` activate hook creates a `SKILL` task when `task-lock` is set.
`task-lock` is **inherited along `extends` chains** — a skill that `extends:
action:jvagent/interview` is task-locked because the base interview SKILL.md
declares it; it need not restate `task-lock: true`.

**`lock-companions:`** — secondary capabilities a `task-lock` skill tolerates
**without releasing the lock**. A list of tool-name globs (`faq__*`, `find_tool`)
and/or **non-locking** skill names. While locked, the callable surface is the
locked skill's tools + `reply`/`respond` **plus** the companions' tools — and
`use_skill` (gated to the companion skills) when any companion skills are listed.
The turn-lock procedure block advertises the companions and instructs the model
to handle the side request, then return to the active step; the bound Action's
`prepare_task_lock_turn` re-grounds the pending step each turn so the interview
resumes automatically. Guards: a companion that is itself `task-lock` is rejected
(it must not seize the lock), and during a lock `use_skill` may only target a
companion or the locked skill itself — switching to an unrelated skill is
blocked. Inherited **additively** along `extends` chains. Example:

```yaml
task-lock: true            # (or inherited via extends: action:jvagent/interview)
lock-companions:
  - faq                    # a non-locking skill (use_skill allowed during the lock)
  - find_tool              # a tool-name glob
```

**Lifecycle binding** (which Action owns skill hooks) is separate from the
`requires-actions` gate. Resolution order in `action_for_skill()`:

1. `extends: action:<namespace>/<action>` — match enabled action by package ref
2. Sole required Action implementing lifecycle hooks (`on_skill_activate`,
   `prepare_task_lock_turn`, `task_lock_runtime_ready`, `needs_task_lock_rebootstrap`,
   `resolve_task_lock_skill`)
3. First name in the skill's `requires-actions` declaration order

`agent.yaml` action list order does **not** affect binding. List API or helper
actions in `requires-actions` when the skill depends on their tools; use
`extends: action:…` on interview skills so `InterviewAction` keeps hook ownership.

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

1. Built-in pure: `jvagent/skills/*`
2. Core action skills: `<action_dir>/skills/*` for actions on the agent
3. App pure: `agents/<ns>/<agent_id>/skills/*` (overrides built-in by name)
4. App action overlays: `agents/.../actions/<ns>/<action>/skills/*` (overrides core action skill by name)
5. **Host providers** (optional):
embedders register callables via `register_host_skill_provider()` in
`jvagent.action.orchestrator.skill_providers`; merged after filesystem discovery
(filesystem wins on name collision). Integral documents the workspace overlay pattern in `docs/backend/workspace-agent-profile.md`.

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

**Agent skill (default):** `jvagent skill add <ns>/<agent> <name>` or manually
create `agents/.../skills/<name>/`. Use `spec: jv`, reference tools in
`allowed-tools`, write the SOP. Declare `requires-actions` when the skill
hard-gates on specific actions. Add `scripts/custom_tools.py` for interview
hooks or other action-coordinated logic. Set `extends: action:<namespace>/<action>`
when composing a base action SOP. Read [`docs/thin-harness.md`](../../docs/thin-harness.md)
before changing orchestrator or action harness code; for interview skills also
[`interview/docs/thin-harness.md`](../action/interview/docs/thin-harness.md).

**Library skill:** `jvagent/skills/<name>/` for framework-shipped reusables only.

**Action-bundled skill:** `<action_dir>/skills/<name>/` only when the skill is
part of the action package you ship (core plugin or `agents/.../actions/...`).

**Claude skill:** `agents/.../skills/<name>/` (or library) with `spec: claude`, add
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

- [ADR-0023 placement standard](../../.planning/adr/0023-skill-placement-standard.md)
- [Integral skill profile](../../.planning/reference/integral-skill-profile.md) — Integral platform extension (7-section bar, `integral_*` namespace, manifest sync)
- [Orchestrator](../../docs/ORCHESTRATOR.md) — the loop and skill lifecycle
- [`jvagent/code_execution`](../action/code_execution) — the sandbox substrate
- [`jvagent/core/sandbox.py`](../core/sandbox.py) — the per-user FS convention
- [`MCPAction` README](../action/mcp/README.md) — external tool servers
