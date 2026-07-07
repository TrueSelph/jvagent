# LeadGenAction

Conversational lead capture for orchestrator agents — interview-style skill foundation without turn-lock.

Capturing contact details is treated as a **standing goal**: after helping, the
agent proactively asks for the next missing field (name → email/phone), tied to
value, and eases off once the required fields are captured. Capture, retrieve,
and sync persist to a single `LeadRecord` per user (recalled across sessions on
return visits).

## Quick start

1. Enable `jvagent/leadgen` and `jvagent/mcp` in `agent.yaml`.
2. Scaffold a skill: `jvagent skill create-leadgen jvagent/my_agent my_leads`
3. Register the skill in orchestrator `skills:`.
4. Configure sync on the `jvagent/leadgen` action in `agent.yaml`
   (`sync_mode` / `sync_min_fields` / `sync_require_any` / `sync_destinations`).
   Destinations are **destination-agnostic** — any MCP server exposing a
   write/append tool (flat file, spreadsheet, email, CRM, DB). A skill may
   instead declare its own `sync:` block to self-contain sync.

## Tools

| Tool | Purpose |
|------|---------|
| `leadgen__capture` | Save partial lead fields; returns `missing_fields` + `next_ask` + `gap_fill_priority`; auto-syncs when thresholds met |
| `leadgen__retrieve` | Load profile + `missing_fields` + `next_ask` + `field_reference` |
| `leadgen__status` | Progress snapshot |
| `leadgen__sync` | Manual sync (`sync_mode: manual`) |

## Docs

- [frontmatter-schema.md](docs/frontmatter-schema.md)
- [extending.md](docs/extending.md)
- [thin-harness.md](docs/thin-harness.md)

## Reference

- Example skill: [examples/example_leadgen/](examples/example_leadgen/)
- Example agent: `examples/jvagent_app/agents/jvagent/leadgen_agent/` — a storefront
  demo where leadgen coexists with a FAQ skill, a product-search skill, and a
  first-message intro; sync targets a creds-free flat-file MCP server.
