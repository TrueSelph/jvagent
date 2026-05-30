# ADR 0011 — Two skill kinds: native SOP overlays vs. Claude bundles

**Status**: Accepted (native SOP overlay shipped for the Executive pattern; Claude-bundle runtime deferred to a future wave)
**Date**: 2026-05-29
**Context**: ADR-0010 (Executive + Centers). Arose from a live-smoke investigation into how skills load into the Skills center.

## Context

jvagent surfaces executable capability two ways, and "skill" was overloaded across them:

- **Action tools** — an `Action.get_tools()` returns provider-agnostic `Tool`s (e.g. `web_search__search`). This *is* capability: a verb the agent can execute. The Executive's Skills center already wraps these into its tool surface (`_build_agent_tools`).
- **Skill bundles** — `SKILL.md`-based packages discovered by `jvagent.scaffold.skill_resolve`.

The realization that drove this ADR: **a skill is not capability — it is judgment over capability.** A skill provides (1) a standard operating procedure that coordinates several tools toward an outcome, and (2) guidance on the reasoned use of a tool the agent already has. If an action already exposes the executable tool, a skill that *also* bundled its own executable would be duplicative.

## Decision

Recognize **two skill kinds on one axis — "who executes":**

### 1. jvagent-native skill (SOP overlay) — shipped

A `SKILL.md` whose body is a **procedure that references existing action tools by their canonical `namespace__tool` name**. It carries no executable code. Execution comes from the agent's installed actions; the skill only adds know-how.

- Discovery: the pattern-neutral `jvagent.scaffold.skill_resolve` (built-in `jvagent.skills` + app-local `agents/<ns>/<agent>/skills/*`), surfaced to the Skills center via `jvagent/action/executive/skills_catalog.py` (`SkillDoc`, `discover_skill_docs`) — self-contained, no cross-imports into other action packages.
- Exposure: the Skills center adds `find_skill` / `use_skill` meta-tools (progressive disclosure). `use_skill` returns the SOP body as an observation, so it persists for the rest of the think-act-observe loop. No change to the executive routing or the one-call-per-tick contract.
- Dependencies: a skill's `allowed-tools` is a **soft dependency** — the skill still activates if a referenced tool is missing, but the Skills center appends a warning so the model won't blindly follow an unexecutable step.
- Config: `skills_source` (`both | local | app | registry | builtin`) + `skills` selector on the Skills center. No explicit per-skill list required.

### 2. Claude skill (self-contained bundle) — future wave

A portable `SKILL.md` + bundled scripts/resources that execute in a **sandboxed filesystem runtime**, by design not assuming any host tool exists. Supporting these needs machinery jvagent's Executive does not yet have:

- bundle/resource format handling (file tree, `allowed-tools`, resource paths),
- a sandboxed execution runtime for bundled scripts,
- resource lifecycle + isolation.

This is explicitly **out of scope** for the Executive pattern's first cut and gets its own ADR + wave when prioritized. Note: a *pure-SOP* Claude skill (no scripts) collapses into kind 1 — it can reference host tools the same way. The wave is specifically for **script-bundling** skills.

## Consequences

- **Positive**: no duplication between actions and skills; skills stay thin (judgment, not capability); discovery reuses neutral infrastructure; isolation preserved; the model is legible ("tools = can-do; skills = how-to-do-well").
- **Negative**: a name-referenced tool is a soft dependency that can drift if an action is uninstalled — mitigated by the activate-time warning (and, optionally later, a hard pre-filter).
- **Neutral**: the script-bundling Claude-skill runtime remains unbuilt; tracked as a named future wave.

## References
- [`adr/0010-executive-centers-architecture.md`](0010-executive-centers-architecture.md)
- [`jvagent/scaffold/skill_resolve.py`](../../jvagent/scaffold/skill_resolve.py) — neutral discovery
- [`jvagent/action/executive/skills_catalog.py`](../../jvagent/action/executive/skills_catalog.py) — executive-local catalog
- [`docs/EXECUTIVE.md`](../../docs/EXECUTIVE.md)
