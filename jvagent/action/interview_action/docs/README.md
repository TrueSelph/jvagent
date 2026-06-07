# Interview Action — Documentation Index

Documentation for `jvagent/interview_action` (`InterviewAction` + skills-v2 interview skills).

## Start here

| Document | Description |
|----------|-------------|
| [../README.md](../README.md) | **Primary reference** — architecture, `interview.yaml`, `SKILL.md`, tools, response envelope, builtin validators, live skill patterns, testing checklist |
| [../CLAUDE.md](../CLAUDE.md) | Agent entry point — file map, invariants, quick start |
| [../example/example_interview/](../example/example_interview/) | Reference skill demonstrating every contract feature |

## Focused guides

| Document | Description |
|----------|-------------|
| [multi-turn-flow.md](multi-turn-flow.md) | Multi-turn conversation lifecycle — activation, turn-lock, tool chaining, review/complete/cancel |
| [extending.md](extending.md) | Extension points — custom validators, pre/post tools, review/completion handlers, LLM tools, field seeding |
| [troubleshooting.md](troubleshooting.md) | Common issues — validation, hooks, tool exposure, session state |
