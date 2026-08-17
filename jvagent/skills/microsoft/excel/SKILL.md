---
name: excel
description: >-
  Read and update Excel workbooks on OneDrive. Use when the user asks to look
  up, append, share, or change spreadsheet data.
requires-actions:
  - MicrosoftExcelAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - excel__read_spreadsheet
  - excel__update_spreadsheet
  - excel__append_spreadsheet
  - excel__create_spreadsheet
  - excel__delete_spreadsheet
  - excel__create_worksheet
  - excel__update_worksheet
  - excel__delete_worksheet
  - excel__batch_clear
  - excel__share_spreadsheet
---

# Excel

Use these tools for spreadsheet work. Do not invent cell values.

Pass **only** the parameters listed for each tool. Omit unused optionals.
Do not invent argument names.

If the user does not name a workbook, omit `spreadsheet_url_or_id` so the
action default is used. Ranges are A1 (`Sheet1!A1:D10`, or a local fragment
plus `worksheet_title`).

Read first when the user asks what is on a sheet. Append for new rows; update
to overwrite a known range. Confirm before `excel__delete_spreadsheet`.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/microsoft_365/auth?service=excel`. Do not send users to
`/api/microsoft/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### excel__read_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional) — A1 notation
- `worksheet_title` (optional)

### excel__update_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional)
- `values` (optional) — 2D array
- `value_input_option` (optional) — `RAW` or `USER_ENTERED`
- `worksheet_title` (optional)

### excel__append_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional)
- `values` (optional) — 2D array
- `value_input_option` (optional)
- `worksheet_title` (optional)

### excel__create_spreadsheet

- `title` (required)

### excel__delete_spreadsheet

- `spreadsheet_url_or_id` (optional)

### excel__create_worksheet

- `title` (required)
- `spreadsheet_url_or_id` (optional)
- `rows` (optional)
- `cols` (optional)

### excel__update_worksheet

- `worksheet_title` (required)
- `spreadsheet_url_or_id` (optional)
- `new_title` (optional)
- `rows` (optional)
- `cols` (optional)
- `hidden` (optional)
- `tab_color` (optional)

### excel__delete_worksheet

- `worksheet_title` (required)
- `spreadsheet_url_or_id` (optional)

### excel__batch_clear

- `spreadsheet_url_or_id` (optional)
- `ranges` (optional) — list of A1 ranges
- `worksheet_title` (optional)

### excel__share_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `share_type` (optional) — `link` or `email`
- `link_scope` (optional)
- `email` (optional)
- `role` (optional)
