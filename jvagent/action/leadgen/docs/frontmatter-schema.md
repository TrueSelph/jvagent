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

## Action-level overrides

`LeadGenAction` attributes `default_fields` and `sync_destinations` apply when the skill omits them.
