---
name: product_inquiry_leads
description: >-
  Capture product inquiry leads conversationally and sync to configured MCP
  destinations when name plus phone or email are collected.
extends: action:jvagent/leadgen
requires-actions:
  - LeadGenAction
  - MCPAction
always-active: true
allowed-tools: []
tags:
  - leadgen
  - sales
leadgen:
  title: Product Inquiry Leads
  fields:
    - key: name
      required: true
      guidance: Full name
      validator: person_name
    - key: organization
      required: false
      guidance: Company name (use decline_value Personal for personal inquiries)
      decline_value: Personal
    - key: email
      required: true
      guidance: Email address
      validator: email
      decline_value: "N/A"
    - key: phone
      required: false
      guidance: Phone number
      validator: phone
    - key: interested_products
      required: false
      guidance: Products or services of interest
      merge: true
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

## Domain rules

- Product questions count as buying intent — capture `interested_products` when asked.
- Never expose sync status or raw profile data to the user.
