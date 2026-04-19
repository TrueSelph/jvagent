---
name: microsoft_excel
description: Read, write, and manage Excel workbooks.
requires-actions:
  - MicrosoftExcelAction
allowed-tools:
  - read_spreadsheet
  - update_spreadsheet
  - append_spreadsheet
  - batch_clear
  - create_spreadsheet
  - create_worksheet
  - update_worksheet
  - delete_worksheet
  - share_spreadsheet
  - delete_spreadsheet
version: 1
tags:
  - spreadsheets
  - microsoft
---

## Workflow

1. Determine the Excel operation the user needs (read, write, structural, or sharing).
2. Use the appropriate tool to perform the operation.
3. Format the results clearly for the user.

### Reading Data

- Use `read_spreadsheet` to retrieve values from a worksheet.
- Specify `spreadsheet_url_or_id` and optionally `range_name` and `worksheet_title` to scope the read.

### Writing / Updating Data

- Use `update_spreadsheet` to overwrite values in a specific range.
- Use `append_spreadsheet` to add rows after the last row of data.
- Use `batch_clear` to clear one or more ranges of values.

### Structural Operations

- Use `create_spreadsheet` to create a new workbook (requires a title).
- Use `create_worksheet` to add a worksheet to an existing workbook.
- Use `update_worksheet` to rename or resize a worksheet.
- Use `delete_worksheet` to remove a worksheet from a workbook.
- Use `delete_spreadsheet` to delete an entire workbook.

### Sharing

- Use `share_spreadsheet` to share a workbook via link or with a specific email.

### Constraints

- Always confirm with the user before deleting worksheets or spreadsheets.
- When writing data, prefer `value_input_option="RAW"` unless the user explicitly wants formula evaluation (use `"USER_ENTERED"`).
- Default to the first worksheet if `worksheet_title` is not specified.