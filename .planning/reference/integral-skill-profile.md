# Integral skill profile — platform extension to JV skills

Integral ships **JV skills** (`spec: jv`) that coordinate the `integral_*` MCP tool surface via `EmbeddedIntegralAction`. This document is the jvagent-side pointer to Integral's extra authoring bar; it does not change the base JV / Anthropic contract.

**Canonical Integral contract:** [integral `skill-format-standard.md`](../../../../integral/docs/backend/skill-format-standard.md) (in the integral repo).

**Base JV skill standard:** [`jvagent/skills/README.md`](../../jvagent/skills/README.md).

---

## What Integral adds on top of jvagent

| Extension | Purpose |
|-----------|---------|
| `extends: action:integral/embedded_integral_action` | Prepends shared Integral tool discipline (propose/stage, scope, staging signals) |
| `requires-actions: [EmbeddedIntegralAction]` | Hard gate on the embedded MCP surface |
| `integral_*` snake_case names | Tool catalogue namespace; exception to hyphen-only Claude name guidance |
| **7-section SOP body** | Public skills must include When to use, delegate, grounding, procedure, staging, forbidden, example |
| Manifest sync | `profile.yaml` `tools_required` + `description` mirrored from `SKILL.md` |

---

## Discovery vs activation (do not conflate)

- **`description` (frontmatter)** — Anthropic/jvagent discovery metadata. Third person. What the skill does + when to select it. Shown in Integral's skill editor as *"When should the agent use this?"*
- **`## When to use` (body)** — Activation-time routing detail: example intents, signals, boundaries. Written after `use_skill`, not for orchestrator indexing.

---

## Placement

| Kind | Path |
|------|------|
| Core `integral_*` | `integral/agent/.../embedded_integral_action/skills/integral_*/` (action-backed per ADR-0023) |
| App bundle overlay | `integral/backend/app/profiles/<slug>/skills/<key>/` |
| Base SOP (not a skill) | `embedded_integral_action/SKILL.md` |

---

## Compliance (Integral repo)

- `integral/backend/app/services/skill_compliance.py`
- `pytest integral/backend/tests/test_skill_compliance.py`
- Normalize frontmatter: `python3 integral/backend/scripts/normalize_skill_frontmatter.py --write`

---

## See also

- ADR-0020 — `extends` SOP inheritance
- ADR-0023 — skill placement
- ADR-0017 — `spec: jv` vs `spec: claude`
- Integral INVARIANTS I-SKILL-01..04
