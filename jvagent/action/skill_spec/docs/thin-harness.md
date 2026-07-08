# Skill spec thin harness profile

**Skill-spec foundation profile** of the jvagent-wide **[thin harness principle](../../../../docs/thin-harness.md)**. Platform rules apply everywhere; domain actions (interview, leadgen, …) add their own profiles on top.

## What lives here

`jvagent/action/skill_spec/` holds shared parsing, registry, and contract-validation primitives extracted from duplicate action stacks. It is **domain-agnostic** — no field names, validators, or business hooks.

| Module | Role |
|--------|------|
| `base.py` | `SkillToolDef`, YAML helpers, frontmatter block loading |
| `registry.py` | `BaseSkillRegistry` — scan skill dirs, cache specs |
| `contract.py` | `custom_tools.py` reference validation helpers |

## Domain profiles

- **[Interview profile](../../interview/docs/thin-harness.md)** — `InterviewAction`, `interview:` frontmatter, eight fixed tools
- **Platform principle:** **[docs/thin-harness.md](../../../../docs/thin-harness.md)**

When extending a code-execution action, keep domain logic in skill extensions (`SKILL.md` + `scripts/custom_tools.py`); extend this foundation only for cross-action shared plumbing.
