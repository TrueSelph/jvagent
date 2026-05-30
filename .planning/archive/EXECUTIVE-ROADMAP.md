# SkillExecutive Roadmap

Living roadmap for the **SkillExecutive** pattern ([`adr/0012-skill-executive-architecture.md`](../adr/0012-skill-executive-architecture.md)). Supersedes the ADR-0010 Executive + Centers roadmap.

## Status

The core orchestrator (`jvagent/action/skill_executive/`) is implemented and covered by tests under `tests/action/skill_executive/`. The scaffold default profile (`executive`) installs `jvagent/skill_executive` + `jvagent/reply`.

## Done

- Single orchestrator at weight `-200` (no centers, no reflex)
- Unified tool surface (IA-as-tools, reply/respond, core tools, skills, catalog)
- Configurable flow continuation (`lock_active_flow`)
- Native SOP skills (`find_skill` / `use_skill`)
- ReplyAction egress (ADR-0014)
- Reference agent: `examples/jvagent_app/agents/jvagent/executive_agent/`

## Follow-ups

See [`docs/EXECUTIVE.md`](../../docs/EXECUTIVE.md) § Known follow-ups:

- Self-contained Claude skill bundles (separate substrate; ADR-0011)
- First-entry routing accuracy and trivial-turn latency measurement at rollout
- Live-provider smoke + performance ledger entry
