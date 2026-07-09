# ADR-0023 — Skill placement standard

**Status**: Accepted
**Date**: 2026-06-08
**Relation**: Supersedes the placement rules in [ADR-0031](0031-skill-sop-extends.md) §2.1 (deprecation warning and “pure SOP only” agent-folder constraint). Preserves ADR-0031 `extends` composition and discovery merge order.

---

## 1. Context

ADR-0020 introduced action overlays (`agents/.../actions/<ns>/<action>/skills/`) and briefly required action-backed skills to live there. That split agent skills across two folders and confused authors (“does `signup_interview` belong under `interview_action` or `skills/`?”).

Runtime already supports multiple discovery tiers; the missing piece is a **single authoring rule** that matches how CLI scaffolding (`jvagent skill add`) and production apps (Zoon, orchestrator example) are organized.

## 2. Decision

### 2.1 Default — agent skills folder

**All skills authored for an agent MUST live under:**

```
agents/<namespace>/<agent_id>/skills/<skill_name>/
```

This applies regardless of skill spec (`jv` or `claude`), `requires-actions`, `extends`, interview `interview:` frontmatter, or bundled `scripts/`.

Examples: `web_lookup`, `signup_interview`, `onboarding_interview`, `docx`, `faq`.

### 2.2 Exceptions (only these)

| Case | Location | Discovered as skill? |
|------|----------|-------------------|
| **Framework library skill** | `jvagent/skills/<name>/` | Yes (`source: builtin`) |
| **Action base SOP** | `<action_dir>/SKILL.md` | **No** — `extends: action:…` composition source only |
| **Skill shipped with a core/custom action package** | `<action_dir>/skills/<name>/` or `agents/.../actions/<ns>/<action>/skills/<name>/` | Yes — only when the skill is **part of the action distribution**, not a general agent SOP |

**Action-bundled** means: the skill is versioned and released together with the action implementation (framework plugin or app-local custom action under `agents/.../actions/`). Interview hooks in `scripts/custom_tools.py` do **not** by themselves justify an action overlay — copy the skill package to `agents/.../skills/` and declare `extends: action:jvagent/interview`.

**Base action SOP** means: one `SKILL.md` at the action package root (e.g. `jvagent/action/interview/SKILL.md`) that child skills inherit via `extends`. It is never listed in the orchestrator skill index.

### 2.3 What is discouraged

Placing general agent skills under `agents/.../actions/jvagent/<installed_action>/skills/` when that action is only a **dependency** on the agent (e.g. `jvagent/interview`, `jvagent/serper_web_search`). Use the agent `skills/` folder instead.

Discovery still scans overlay paths for backward compatibility and for true action bundles; new work should follow §2.1.

### 2.4 Discovery merge order (unchanged)

1. Builtin library (`jvagent/skills/*`)
2. Core action skills (`<action_dir>/skills/*` for actions on the agent)
3. App-local agent skills (`agents/.../skills/*`)
4. App action overlays (`agents/.../actions/.../skills/*`)

App-local overrides built-in / core by name.

## 3. Consequences

- **Positive**: One obvious drop zone per agent; aligns with `jvagent skill add`, examples, and Zoon; interview and orchestrator skills co-locate.
- **Negative**: Existing overlay-only layouts remain valid at runtime but should migrate to `agents/.../skills/` for consistency.
- **Neutral**: `extends`, `requires-actions`, and `Action.resolve_skill_scan_dirs()` behavior unchanged.

## 4. References

- [`jvagent/skills/README.md`](../../jvagent/skills/README.md) — canonical authoring guide
- [`docs/scaffolding.md`](../../docs/scaffolding.md) — CLI + tiers
- [`jvagent/scaffold/skill_resolve.py`](../../jvagent/scaffold/skill_resolve.py) — resolver implementation
