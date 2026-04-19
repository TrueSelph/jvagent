---
name: microsoft_excel
description: Read, write, and manage Excel workbooks.
requires-actions:
  - MicrosoftExcelAction
allowed-tools:
  - microsoft_excel__read_spreadsheet
  - microsoft_excel__update_spreadsheet
  - microsoft_excel__append_spreadsheet
  - microsoft_excel__batch_clear
  - microsoft_excel__create_spreadsheet
  - microsoft_excel__create_worksheet
  - microsoft_excel__update_worksheet
  - microsoft_excel__delete_worksheet
  - microsoft_excel__share_spreadsheet
  - microsoft_excel__delete_spreadsheet
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

- Use `microsoft_excel__read_spreadsheet` to retrieve values from a worksheet.
- Specify `spreadsheet_url_or_id` and optionally `range_name` and `worksheet_title` to scope the read.

### Writing / Updating Data

- Use `microsoft_excel__update_spreadsheet` to overwrite values in a specific range.
- Use `microsoft_excel__append_spreadsheet` to add rows after the last row of data.
- Use `microsoft_excel__batch_clear` to clear one or more ranges of values.

### Structural Operations

- Use `microsoft_excel__create_spreadsheet` to create a new workbook (requires a title).
- Use `microsoft_excel__create_worksheet` to add a worksheet to an existing workbook.
- Use `microsoft_excel__update_worksheet` to rename or resize a worksheet.
- Use `microsoft_excel__delete_worksheet` to remove a worksheet from a workbook.
- Use `microsoft_excel__delete_spreadsheet` to delete an entire workbook.

### Sharing

- Use `microsoft_excel__share_spreadsheet` to share a workbook via link or with a specific email.

### Constraints

- Always confirm with the user before deleting worksheets or spreadsheets.
- When writing data, prefer `value_input_option="RAW"` unless the user explicitly wants formula evaluation (use `"USER_ENTERED"`).
- Default to the first worksheet if `worksheet_title` is not specified.

## Scope

This skill is for Microsoft Excel workbook operations (read/write, worksheet structure, share/delete). Use it for spreadsheet work in Microsoft ecosystems. Do not use it for OneDrive file inventory tasks, mail/calendar actions, or unrelated research queries.

## Grounding

- Only report workbook IDs, worksheet names, ranges, and values that tool responses actually returned.
- If reads return no rows/cells, state that explicitly instead of inferring likely spreadsheet content.
- Always confirm destructive actions (`microsoft_excel__delete_worksheet`, `microsoft_excel__delete_spreadsheet`) before calling tools.