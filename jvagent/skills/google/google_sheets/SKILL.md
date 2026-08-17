---
name: google_sheets
description: >-
  Read and update Google Sheets. Use when the user asks to look up,
  append, format, merge, share, or change spreadsheet data.
requires-actions:
  - GoogleSheetsAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - google_sheets__read_spreadsheet
  - google_sheets__update_spreadsheet
  - google_sheets__append_spreadsheet
  - google_sheets__create_spreadsheet
  - google_sheets__delete_spreadsheet
  - google_sheets__create_worksheet
  - google_sheets__update_worksheet
  - google_sheets__delete_worksheet
  - google_sheets__merge_cells
  - google_sheets__unmerge_cells
  - google_sheets__format_cells
  - google_sheets__last_filled_row
  - google_sheets__batch_clear
  - google_sheets__share_spreadsheet
---

# Google Sheets

Use these tools for spreadsheet work. Do not invent cell values.

Pass **only** the parameters listed for each tool. Omit unused optionals.
Do not invent argument names (no `spreadsheet_id`, `sheet`, `range`, `data`).

If the user does not name a spreadsheet, omit `spreadsheet_url_or_id` so the
action default is used. Ranges are A1 (`Sheet1!A1:D10`, or a local fragment
plus `worksheet_title`).

Read first when the user asks what is on a sheet. Append for new rows; update
to overwrite a known range. Confirm before `google_sheets__delete_spreadsheet`.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/google_workspace/auth?service=sheets`. Do not send users to
`/api/google/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### google_sheets__read_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional) — A1 range; omit for the whole tab
- `worksheet_title` (optional)

### google_sheets__update_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional) — A1 range to overwrite
- `values` (optional) — 2D array of cell values
- `value_input_option` (optional) — `RAW` or `USER_ENTERED`
- `worksheet_title` (optional)

### google_sheets__append_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional) — A1 anchor; omit to append after the table
- `values` (optional) — 2D array of rows to append
- `value_input_option` (optional) — `RAW` or `USER_ENTERED`
- `worksheet_title` (optional)

### google_sheets__create_spreadsheet

- `title` (required)

### google_sheets__delete_spreadsheet

- `spreadsheet_url_or_id` (optional)

### google_sheets__create_worksheet

- `title` (required)
- `spreadsheet_url_or_id` (optional)
- `rows` (optional)
- `cols` (optional)

### google_sheets__update_worksheet

- `worksheet_title` (required) — current tab name
- `spreadsheet_url_or_id` (optional)
- `new_title` (optional)
- `rows` (optional)
- `cols` (optional)
- `hidden` (optional)
- `tab_color` (optional) — `{"red": 0-1, "green": 0-1, "blue": 0-1}`

### google_sheets__delete_worksheet

- `worksheet_title` (required)
- `spreadsheet_url_or_id` (optional)

### google_sheets__merge_cells

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional)
- `worksheet_title` (optional)
- `merge_type` (optional) — `MERGE_ALL`, `MERGE_ROWS`, or `MERGE_COLUMNS`

### google_sheets__unmerge_cells

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional)
- `worksheet_title` (optional)

### google_sheets__format_cells

- `spreadsheet_url_or_id` (optional)
- `range_name` (optional)
- `worksheet_title` (optional)
- `user_entered_format` (optional) — format dict
- `fields` (optional) — field mask, e.g. `userEnteredFormat`

### google_sheets__last_filled_row

- `spreadsheet_url_or_id` (optional)
- `column` (optional) — column letter, default `A`
- `worksheet_title` (optional)

### google_sheets__batch_clear

- `spreadsheet_url_or_id` (optional)
- `ranges` (optional) — list of A1 ranges
- `worksheet_title` (optional)

### google_sheets__share_spreadsheet

- `spreadsheet_url_or_id` (optional)
- `share_type` (optional) — `link` or `email`
- `link_scope` (optional) — `anyone` or `domain`
- `email` (optional) — required when `share_type` is `email`
- `role` (optional) — `reader`, `writer`, or `owner`
