# Leadgen frontmatter schema

Skills declare a machine contract under `leadgen:` in `SKILL.md` frontmatter.

```yaml
name: my_leads
extends: action:jvagent/leadgen
requires-actions: [LeadGenAction, MCPAction]
always-active: true
leadgen:
  title: My Lead Capture
  summary: Optional description
  fields:
    - key: full_name
      required: true
      guidance: Full name
      validator: person_name
    - key: email
      required: true
      validator: email
      decline_value: "N/A"
    - key: phone
      required: false
      validator: phone
      phone_locale: E164   # or GY
    - key: quote_items
      merge: true
    - key: project_budget
      guidance: Stated budget once a project is described
      relevant_when:
        any_present: [project_description]
  gap_fill:
    batch: true
  sync:
    mode: on_capture       # on_capture | on_complete | manual
    min_fields: [full_name]
    require_any: [phone, email]
    # Destination-agnostic: any MCP server + tool. Below is a flat file; a
    # spreadsheet, email, CRM, or DB is the same shape (only server/tool/args
    # change). Templates: {profile_json}, {profile_row}, {profile_keys},
    # {user_id}, {<field>}.
    destinations:
      - server: leadfile          # any configured MCP server (see MCPAction)
        mode: mcp
        tool: write_file          # the server's write/append tool
        arguments:
          path: "lead.json"
          content: "Lead profile: {profile_json}"
  handlers:
    post_capture: enrich_from_channel
    qualify: check_minimum_budget
    on_sync: notify_sales_team
  skill_tools: []
allowed-tools: []
```

## Field keys

| Key | Type | Description |
|-----|------|-------------|
| `key` | string | Canonical field name (required) |
| `guidance` | string | Model-facing description |
| `required` | bool | Included in `missing_fields` and the sync gates |
| `aliases` | list | Alternate names for capture |
| `validator` | string | Built-in: `email`, `phone`, `phone_e164`, `phone_gy`, `person_name` |
| `validator_args` | map | Passed to validator |
| `decline_value` | string | Value when user refuses (stops re-asking) |
| `merge` | bool | Comma-merge list fields on update |
| `phone_locale` | string | Resolves a built-in `phone_<locale>` validator on any field (`GY`, `E164`); unknown locale falls back to E164 |
| `relevant_when` | map | Optional gate: `{ any_present: [keys], not_declined: [keys] }` — askable only once a listed key is filled, and never while a `not_declined` key holds its own `decline_value`; the gate travels in `field_reference` |

### Capture catalogue (server-nominated ask)

Tool results expose `fields` (saved values), `missing_fields` (required keys still empty),
`next_required` (the single required key to ask now, derived from `missing_fields[0]`),
`optional_fields` (open optional keys whose `relevant_when` passes), `field_reference` (every
declared field in ask order, with guidance and `required`), and `directive` (the ask or hand-off
for this reply). The model follows the directive and applies the five ask tests; there is no
persisted ask pointer, no `ask_next` stamp, and no priority list.

## Sync template tokens

`{user_id}`, `{profile_json}`, `{profile_keys}`, `{profile_row}`, `{<field_key>}`

## Sync location: skill frontmatter vs the action

The `sync:` block above is **optional** in the skill. Sync is more often
configured on the `jvagent/leadgen` **action** in `agent.yaml` — destinations are
deployment/infra, so the skill defines *what* to capture and the agent defines
*where* it syncs:

```yaml
# agent.yaml
- action: jvagent/leadgen
  context:
    enabled: true
    sync_mode: on_capture           # on_capture | on_complete | manual
    sync_min_fields: [full_name]
    sync_require_any: [phone, email]
    sync_destinations:
      - server: leadfile            # any configured MCP server
        mode: mcp
        tool: write_file
        arguments:
          path: "lead.json"
          content: "Lead profile: {profile_json}"
```

Precedence: if the **skill** declares sync destinations, its `sync:` block governs
in full. Otherwise the **action-level** sync config
(`sync_mode` / `sync_min_fields` / `sync_require_any` / `sync_destinations`)
governs the whole sync. `LeadGenAction.default_fields` likewise supplies fields
when the skill declares none.

> Internal, underscore-prefixed keys (e.g. the stored sync digest) are excluded
> from every template and from the change-detection digest, so they never sync out
> and unchanged profiles are not re-synced.
