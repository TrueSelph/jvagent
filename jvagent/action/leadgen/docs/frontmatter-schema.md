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
    - key: name
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
    - key: interested_products
      merge: true
  gap_fill:
    batch: true
    priority: [name, phone, email, organization]
  sync:
    mode: on_capture       # on_capture | on_complete | manual
    min_fields: [name]
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
| `required` | bool | Included in gap-fill / on_complete sync |
| `aliases` | list | Alternate names for capture |
| `validator` | string | Built-in: `email`, `phone`, `phone_e164`, `phone_gy`, `person_name` |
| `validator_args` | map | Passed to validator |
| `decline_value` | string | Value when user refuses (stops re-asking) |
| `merge` | bool | Comma-merge list fields on update |
| `phone_locale` | string | `GY` forces Guyana normalization |

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
    sync_min_fields: [name]
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
