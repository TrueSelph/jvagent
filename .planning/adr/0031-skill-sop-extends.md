# ADR 0031 — Skill SOP inheritance (`extends`) and action-backed placement

**Status**: Accepted
**Date**: 2026-06-07
**Relation**: Extends [ADR-0011](0011-skills-two-kinds.md) (JV SOP overlays) and [ADR-0012](0012-skill-executive-architecture.md) (orchestrator skill lifecycle).

---

## 1. Context

Interview skills previously received a framework-standard tool-loop procedure via a **hard-coded** branch in `discover_skill_docs` that detected `requires-actions: [InterviewAction]` and prepended `sop/standard_procedure.md`. That coupled SOP composition to one action and one heuristic.

Meanwhile, action-dependent skills (interviews with `scripts/custom_tools.py`, `interview:` frontmatter, `requires-actions`) lived in `agents/<ns>/<agent>/skills/` beside pure JV SOPs (`research`, `answer`) and Claude bundles (`docx`, `pdf_generation`) — mixing three different skill roles in one folder.

## 2. Decision

### 2.1 Three-tier skill placement

| Tier | Location | Discovered? |
|------|----------|-------------|
| **Action base SOP** | `<action_dir>/SKILL.md` | No — `extends` composition source only |
| **Action-backed jvskill** | `<action_dir>/skills/<name>/` | Yes — when parent action is on the agent |
| **Agent skill** | `jvagent/skills/` or `agents/.../skills/` | Yes — **pure JV SOP** or **`spec: claude`** only |

App overlays for action-backed skills: `agents/<ns>/<agent>/actions/<namespace>/<action>/skills/<name>/`.

Skills in `agents/.../skills/` that declare `requires-actions` log a **deprecation warning** (backward compatible in v1).

### 2.2 `extends` frontmatter (SOP inheritance)

```yaml
extends: action:jvagent/interview   # base SOP from action-root SKILL.md
extends: skill:base_skill                  # transitive skill inheritance
```

- **`requires-actions`** — unchanged; hard gate for tool-bundle availability. Not SOP inheritance.
- **`extends`** — composes markdown **body only** (base + child custom rules). Never merges frontmatter.
- Composition: `resolve_chain(extends) + "\n\n" + child_body`.
- Resolution lives in `jvagent/scaffold/sop_extend.py`; discovery wiring in `jvagent/scaffold/skill_resolve.py`.

### 2.3 Discovery merge precedence

1. Builtin pure skills (`jvagent/skills/*`)
2. Core action skills (`<action_dir>/skills/*`) for installed actions
3. App pure skills (`agents/.../skills/*`)
4. App action overlays (`agents/.../actions/.../skills/*`)

`skills_source` filtering:
- `app` — `source: app` only (pure + action overlays)
- `library` — `source: builtin` + `source: action` (pure library + core action skills)
- `both` — full merged set

### 2.4 Interview migration

- Base procedure: `jvagent/action/interview/SKILL.md`
- Reference package: `interview/examples/example_interview/` (copy to app `skills/` overlay to activate)
- Implicit interview injection in `discover_skill_docs` **removed**; skills declare `extends: action:jvagent/interview` explicitly.

## 3. Consequences

- **Positive**: Declarative, reusable SOP inheritance for any action that ships a base `SKILL.md`; action-backed skills co-locate with their tool bundle; agent `skills/` folder has a single clear role.
- **Negative**: Existing action-backed skills in deprecated paths need migration; one extra frontmatter key per inheriting skill.
- **Neutral**: `spec: claude` skills unchanged; host skill providers unchanged.

## 4. References

- [`jvagent/scaffold/sop_extend.py`](../../jvagent/scaffold/sop_extend.py)
- [`jvagent/scaffold/skill_resolve.py`](../../jvagent/scaffold/skill_resolve.py)
- [`jvagent/skills/README.md`](../../jvagent/skills/README.md)
- [`jvagent/action/interview/README.md`](../../jvagent/action/interview/README.md)
