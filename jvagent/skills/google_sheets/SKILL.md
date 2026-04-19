---
name: google_sheets
description: Read, write, and manage Google Sheets spreadsheets.
requires-actions:
  - GoogleSheetsAction
allowed-tools:
  - google_sheets__read_spreadsheet
  - google_sheets__last_filled_row
  - google_sheets__update_spreadsheet
  - google_sheets__append_spreadsheet
  - google_sheets__batch_clear
  - google_sheets__format_cells
  - google_sheets__merge_cells
  - google_sheets__unmerge_cells
  - google_sheets__create_spreadsheet
  - google_sheets__create_worksheet
  - google_sheets__update_worksheet
  - google_sheets__delete_worksheet
  - google_sheets__share_spreadsheet
  - google_sheets__delete_spreadsheet
version: 1
tags:
  - spreadsheets
  - google
---

## Workflow

1. Determine the spreadsheet operation the user needs (read, write, format, structural, or sharing).
2. Use the appropriate tool to perform the operation.
3. Format the results clearly for the user.

### Reading Data

- Use `google_sheets__read_spreadsheet` to retrieve cell values from a range or entire worksheet.
- Use `google_sheets__last_filled_row` to find the last row containing data in a given column (useful for appending).

### Writing / Updating Data

- Use `google_sheets__update_spreadsheet` to overwrite cells in a specific range.
- Use `google_sheets__append_spreadsheet` to add rows after the last filled row.
- Use `google_sheets__batch_clear` to clear one or more ranges of cells.

### Formatting

- Use `google_sheets__format_cells` to apply formatting (e.g. bold, background color, number format) to a range.
- Use `google_sheets__merge_cells` to merge a range of cells into a single cell.
- Use `google_sheets__unmerge_cells` to unmerge previously merged cells in a range.

### Structural Operations

- Use `google_sheets__create_spreadsheet` to create a new spreadsheet (requires a title).
- Use `google_sheets__create_worksheet` to add a new worksheet to an existing spreadsheet.
- Use `google_sheets__update_worksheet` to modify worksheet properties (title, dimensions, visibility, tab color).
- Use `google_sheets__delete_worksheet` to remove a worksheet from a spreadsheet.
- Use `google_sheets__delete_spreadsheet` to permanently delete a spreadsheet.

### Sharing

- Use `google_sheets__share_spreadsheet` to share a spreadsheet via link or with a specific email.

### Constraints

- Always confirm with the user before deleting spreadsheets or worksheets.
- Default to the agent's configured spreadsheet if no URL or ID is provided.
- When appending data, prefer using `google_sheets__last_filled_row` first to determine the correct insertion point.

## Scope

This skill is for Google Sheets workbook and worksheet operations, including reads, writes, formatting, structure changes, and sharing. Use it for spreadsheet tasks that target Google Sheets. Do not use it for Drive file lifecycle operations, email, or calendar requests.

## Grounding

- Only report cell values, sheet names, ranges, and sharing outcomes actually returned by tools.
- If a read/query returns empty data, state that the range/sheet had no returned values instead of inventing rows.
- Always confirm destructive operations (`google_sheets__delete_spreadsheet`, `google_sheets__delete_worksheet`, `google_sheets__batch_clear` on broad ranges) before execution.