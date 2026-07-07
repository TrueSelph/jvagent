---
name: example_leadgen
description: >-
  Reference lead capture skill. Collects name, organization, contact info, and
  product interest. Demonstrates leadgen frontmatter, validators, hooks, and MCP sync.
extends: action:jvagent/leadgen
requires-actions:
  - LeadGenAction
  - MCPAction
always-active: true
allowed-tools: []
tags:
  - example
  - leadgen
  - reference
leadgen:
  title: Product Inquiry Leads
  summary: >-
    Reference leadgen skill for product inquiries. Copy to agents/.../skills/<name>/
    and register in agent.yaml orchestrator skills.
  fields:
    - key: name
      required: true
      guidance: Full name of the contact
      validator: person_name
      aliases: [full_name, contact_name]
    - key: organization
      required: false
      guidance: Company or organization name
      decline_value: Personal
    - key: email
      required: true
      guidance: Email address
      validator: email
      decline_value: "N/A"
    - key: phone
      required: false
      guidance: Phone number with country code if possible
      validator: phone
      phone_locale: E164
    - key: interested_products
      required: false
      guidance: Products or services the lead is interested in
      merge: true
    - key: project_description
      required: false
      guidance: Brief description of their project or need
  gap_fill:
    batch: true
    priority: [name, phone, email, organization]
  sync:
    mode: on_capture
    min_fields: [name]
    require_any: [phone, email]
    destinations:
      - server: google_sheets
        mode: mcp
        tool: sheets_append_values
        arguments:
          spreadsheetId: "YOUR_SPREADSHEET_ID"
          range: Leads
          values:
            - "{profile_row}"
  handlers:
    post_capture: enrich_from_channel
---

> **Note:** Reference package under `leadgen/examples/` (not auto-discovered). Copy to `agents/<ns>/<agent>/skills/<name>/` and register in `agent.yaml`.

## Custom instructions

### When to use

- Any conversational agent that should capture leads while helping the user.
- Product inquiries, quote requests, demo bookings.

### Tone

- Weave data collection into helpful answers — never interrogate.
- When the user declines email, save `email="N/A"` immediately.
