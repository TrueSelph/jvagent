---
name: google_sheets
description: Read, write, and manage Google Sheets spreadsheets.
requires-actions:
  - GoogleSheetsAction
allowed-tools:
  - read_spreadsheet
  - last_filled_row
  - update_spreadsheet
  - append_spreadsheet
  - batch_clear
  - format_cells
  - merge_cells
  - unmerge_cells
  - create_spreadsheet
  - create_worksheet
  - update_worksheet
  - delete_worksheet
  - share_spreadsheet
  - delete_spreadsheet
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

- Use `read_spreadsheet` to retrieve cell values from a range or entire worksheet.
- Use `last_filled_row` to find the last row containing data in a given column (useful for appending).

### Writing / Updating Data

- Use `update_spreadsheet` to overwrite cells in a specific range.
- Use `append_spreadsheet` to add rows after the last filled row.
- Use `batch_clear` to clear one or more ranges of cells.

### Formatting

- Use `format_cells` to apply formatting (e.g. bold, background color, number format) to a range.
- Use `merge_cells` to merge a range of cells into a single cell.
- Use `unmerge_cells` to unmerge previously merged cells in a range.

### Structural Operations

- Use `create_spreadsheet` to create a new spreadsheet (requires a title).
- Use `create_worksheet` to add a new worksheet to an existing spreadsheet.
- Use `update_worksheet` to modify worksheet properties (title, dimensions, visibility, tab color).
- Use `delete_worksheet` to remove a worksheet from a spreadsheet.
- Use `delete_spreadsheet` to permanently delete a spreadsheet.

### Sharing

- Use `share_spreadsheet` to share a spreadsheet via link or with a specific email.

### Constraints

- Always confirm with the user before deleting spreadsheets or worksheets.
- Default to the agent's configured spreadsheet if no URL or ID is provided.
- When appending data, prefer using `last_filled_row` first to determine the correct insertion point.