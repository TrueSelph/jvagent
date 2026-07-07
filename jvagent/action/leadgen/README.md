# LeadGenAction

Conversational lead capture for orchestrator agents — interview-style skill foundation without turn-lock.

## Quick start

1. Enable `jvagent/leadgen` and `jvagent/mcp` in `agent.yaml`.
2. Scaffold a skill: `jvagent skill create-leadgen jvagent/my_agent my_leads`
3. Register the skill in orchestrator `skills:`.
4. Configure `leadgen.sync.destinations` for Google Sheets, email MCP, etc.

## Tools

| Tool | Purpose |
|------|---------|
| `leadgen__capture` | Save partial lead fields; auto-sync when thresholds met |
| `leadgen__retrieve` | Load profile + missing_fields + field_reference |
| `leadgen__status` | Progress snapshot |
| `leadgen__sync` | Manual sync (`sync.mode: manual`) |

## Docs

- [frontmatter-schema.md](docs/frontmatter-schema.md)
- [extending.md](docs/extending.md)
- [thin-harness.md](docs/thin-harness.md)

## Reference

- Example skill: [examples/example_leadgen/](examples/example_leadgen/)
- Example agent: `examples/jvagent_app/agents/jvagent/leadgen_agent/`
