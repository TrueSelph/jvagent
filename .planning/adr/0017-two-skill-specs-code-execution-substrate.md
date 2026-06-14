# ADR 0017 — Two skill specs (JV + Claude) and a multitenant code-execution substrate

**Status**: Accepted
**Date**: 2026-05-31
**Relation**: Extends [ADR-0011](0011-skills-two-kinds.md) (two skill kinds) and [ADR-0012](0012-skill-executive-architecture.md) (actions are first-class tools). Builds on the per-user filesystem convention shared with [`MCPAction`](../reference/actions-catalog.md).

---

## 1. Context

The orchestrator discovers skills (`SKILL.md` folders) and activates them by
progressive disclosure. JV skills — SOPs that reference tools Actions/IAs already
furnish — work well. The open question was how a skill provides *new* executable
capability that no action offers.

An earlier attempt let a skill's `scripts/` modules expose a `get_tool_definition()`
/ `get_tools()` protocol that the orchestrator surfaced as typed tools. This was a
**third skill specification** — a jvagent-only variation that diverged from the
Anthropic Agent Skills standard (where bundled scripts are *run* by the agent
through a code-execution/filesystem tool, not registered as typed tools). It was
reverted.

jvagent also differs from Claude Code in a way that constrains any execution
design: it is **inherently multitenant**. Each user has their own branch of the
graph and their own slice of the filesystem; `MCPAction` already runs a separate
filesystem MCP subprocess per user, rooted at `<agent_id>/<user_id>/`.

## 2. Decision

Manage exactly **two skill specs**, distinguished by a `spec` frontmatter key
(default `jv`; unknown values fall back to `jv`):

1. **JV skill** (`spec: jv`) — an SOP that references action/IA tools via
   `allowed-tools` / `requires-actions`. Activation surfaces those tools. No
   executable code. (Unchanged.)
2. **Claude skill** (`spec: claude`) — a standard Anthropic Agent Skills folder.
   Bundled scripts are **run by the model**, not surfaced as typed tools.

Add a multitenant **code-execution substrate** as the runtime Claude skills
assume, rather than a per-skill tool protocol.

### 2.1 `jvagent/core/sandbox.py` — the shared per-user FS service

The per-user sandbox primitives (root/segment resolution, provisioning, path
safety) are promoted from `action/mcp/sandbox.py` into **core** so all consumers
share one service instead of reaching into an action's submodule. Consumers:
`MCPAction` (per-user MCP subprocess roots), the `file_interface` action
(in-process file I/O tools), and the new `code_execution` action.
`resolve_mcp_sandbox_relpath` → `resolve_user_sandbox_relpath` (it was never
MCP-specific); `provision_user_sandbox(agent, user, fi)` returns a ready cwd.

### 2.2 `jvagent/code_execution` — the substrate (opt-in, off by default)

A `CodeExecutionAction` exposes a `code_execution__bash` tool whose working
directory is the **caller's own per-user slice** (resolved from the dispatch
context through `core.sandbox`). Per-user data isolation is therefore inherited
from the same convention the file MCPs and `file_interface` use — three views on
one slice. Artifacts a script writes are immediately visible to the file tools.

Per-execution OS containment comes from a **pluggable `Executor`**. The default
`SubprocessExecutor` enforces wall-clock + CPU + memory + file-size + process
limits, a scrubbed environment (no inherited host secrets), and a cwd confined to
the user's slice. It is **not a hard security boundary** (no network/filesystem
namespace isolation); deployments running untrusted/third-party skills should
supply a container/jail backend satisfying the same protocol. Enabled per agent;
**off by default**.

### 2.3 Activation staging

On `use_skill` of a `spec: claude` skill, the orchestrator stages the skill
folder at `.skills/<name>/` inside the caller's slice and appends a note telling
the model to run its scripts via `code_execution__bash`. JV skills are untouched
(they surface referenced tools). This is the only orchestrator-facing branch on
`spec`.

### 2.4 Capability skills reclassified

The former `scripts/`-bearing library skills are resolved into the two specs:

- `pdf_generation`, `triage` → **Claude skills** (bundled CLI scripts run via the
  substrate; `pdf_generation` is the canonical example — a user renders a PDF into
  their own slice).
- `fileinterface` → **`jvagent/file_interface` Action** (it *is* the per-user
  filesystem; its ops are first-class tools JV skills can reference).
- `skill_hub` → **`jvagent/skill_hub` Action** (it mutates per-agent config and
  needs jvagent internals — not user-sandbox code).

No skill exposes typed tools via a bundled protocol anymore.

## 3. Consequences

- **Standard alignment.** Claude skills are drop-in with the agentskills.io
  standard; bundled scripts run the Anthropic way (filesystem + code execution).
- **Multitenancy preserved.** Execution reuses the established per-user wall; no
  user can see or touch another's slice.
- **Security is opt-in and honest.** Code execution is off by default; the
  default backend's limits are documented as pragmatic, not a jail. Untrusted
  skills require an isolating backend.
- **One mental model.** Two specs, both `SKILL.md` + progressive disclosure;
  capability that isn't a skill lives in an Action (ADR-0012).
- **Non-local storage.** The subprocess backend needs a real directory, so v1
  requires local file storage; object-storage backends need a materialize/sync
  layer (future work).

## 4. Alternatives considered

- **Skill `scripts/` as typed tools** (the reverted third spec) — rejected: a
  jvagent-only variation, diverges from the standard, and conflates "skill" with
  "tool host" (which is an Action's job).
- **No execution; capability only via Actions/MCP** — rejected: cannot run
  third-party Claude skills that bundle scripts, the standard's core affordance.
- **Container-only execution** — rejected as the *only* mode: too heavy a
  dependency for many deployments. Kept as a pluggable backend instead.
