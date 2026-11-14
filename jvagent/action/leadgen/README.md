# LeadGenAction

Conversational lead capture for orchestrator agents — interview-style skill foundation without turn-lock.

Capturing profile fields is treated as a **standing goal**: after helping, the
agent proactively asks for the next missing required field, tied to value, and
eases off once required fields are captured. Capture, retrieve, and sync persist
to a single `LeadRecord` per user (recalled across sessions on return visits).
Field names, hand-off wording, and sync gates come from the skill — the core is
agent-agnostic.

## Quick start

1. Enable `jvagent/leadgen` and `jvagent/mcp` in `agent.yaml`.
2. Scaffold a skill: `jvagent skill create-leadgen jvagent/my_agent my_leads`
3. Register the skill in orchestrator `skills:`.
4. Configure sync on the `jvagent/leadgen` action in `agent.yaml`
   (`sync_mode` / `sync_min_fields` / `sync_require_any` / `sync_destinations`).
   Destinations are **destination-agnostic** — any MCP server exposing a
   write/append tool (flat file, spreadsheet, email, CRM, DB). A skill may
   instead declare its own `sync:` block to self-contain sync.

## Flexibility

The action carries **no domain field vocabulary**: field names, `required`,
validators, `capture_only`, `merge`, `relevant_when`, `label`, sync gates,
`messages`, `channel_defaults`, and handlers come only from the skill's
`leadgen:` block. Domain rewriting (item lines, order vs quote intent) belongs
in skill hooks — never in the capture engine. `capture_only` fields are
captured only when volunteered. `phone_locale` resolves a built-in
`phone_<locale>` when no explicit `validator` is set (for example `GY`); an
unknown locale falls back to E164. Sync gates and channel autofill default to
empty. Refusals use the `decline_value` sentinel (any language).

## Tools

| Tool | Purpose |
|------|---------|
| `leadgen__capture` | Save partial lead fields; returns `status`, `fields_saved`, `fields`, `missing_fields`, `next_required`, `optional_fields`, `field_reference`, `validation_errors`, `sync_result`, and the ask `directive`; auto-syncs when thresholds met |
| `leadgen__retrieve` | Load profile + `missing_fields`, `next_required`, `optional_fields`, `field_reference`, and the ask `directive` |
| `leadgen__status` | Progress snapshot |
| `leadgen__sync` | Manual sync (`sync_mode: manual`) |

## Docs

- [frontmatter-schema.md](docs/frontmatter-schema.md)
- [extending.md](docs/extending.md)
- [thin-harness.md](docs/thin-harness.md)
- [troubleshooting.md](docs/troubleshooting.md)

## Reference

- Example skill: [examples/example_leadgen/](examples/example_leadgen/)
- Example agent: `examples/jvagent_app/agents/jvagent/leadgen_agent/` — a storefront
  demo where leadgen coexists with a FAQ skill, a product-search skill, and a
  first-message intro; sync targets a creds-free flat-file MCP server.
