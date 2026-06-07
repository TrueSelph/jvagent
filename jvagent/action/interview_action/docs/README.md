# Interview Action — documentation index

How-to and reference guides for `jvagent/interview_action`. **Framework SOP templates** (runtime-composed procedure, authoring scaffolds) live in [`../sop/`](../sop/) — not here.

## Start here

| Document | Description |
|----------|-------------|
| [../README.md](../README.md) | **Primary reference** — architecture, `interview:` contract, `SKILL.md`, tools, response envelope, live skill patterns, checklist |
| [../CLAUDE.md](../CLAUDE.md) | Agent guide — foundation vs skill layers, invariants, quick file map |
| [../sop/README.md](../sop/README.md) | SOP assets — `standard_procedure.md` (runtime) + `skill_custom_instructions.md` (authoring) |

## Guides

| Document | Description |
|----------|-------------|
| [multi-turn-flow.md](multi-turn-flow.md) | Turn-by-turn lifecycle, turn-lock, session states, branching |
| [extending.md](extending.md) | Validators, pre/post tools, review/completion, LLM custom tools |
| [troubleshooting.md](troubleshooting.md) | Common failures and fixes |

## Reference implementation

| Path | Description |
|------|-------------|
| [../example/example_interview/](../example/example_interview/) | Copy to `skills/<name>/` to create a live skill |
| zoon-ai `agents/zoon/zoon_ai/skills/onboarding_interview/` | Production onboarding + phone-update |
| zoon-ai `agents/zoon/zoon_ai/skills/pre_alert_interview/` | Production tracking / pre-alert |
| jvagent `examples/.../skills/signup_interview/` | Demo signup interview |
