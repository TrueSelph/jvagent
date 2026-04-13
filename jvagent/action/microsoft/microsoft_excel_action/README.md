# Microsoft Excel Action

Read and update **Excel workbooks stored on OneDrive** using Microsoft Graph **Excel REST** (workbook session, ranges, worksheets). Subclasses `MicrosoftAction` for Entra ID OAuth.

Workbooks must be reachable as **`/me/drive/items/{itemId}`**. Resolve the item id from a sharing URL (path segment `/items/{id}`), or pass the raw id.

Shared setup: [Microsoft actions README](../README.md). Scopes match OneDrive read/write: `offline_access`, `User.Read`, `Files.ReadWrite.All`.

## Action attributes

| Attribute | Description |
| --------- | ----------- |
| `spreadsheet_url` | Default workbook: OneDrive item id or URL containing `/items/...` |
| `worksheet_title` | Default sheet tab when a range omits an explicit sheet (default `Sheet1`) |

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/microsoft_excel_action
  context:
    spreadsheet_url: ""      # optional default workbook id/URL
    worksheet_title: Sheet1
```

## REST API

**OAuth:** `GET /api/microsoft/{action_id}`.

**Spreadsheet routes** are the same paths as Google Sheets (`google_sheets_action/endpoints.py`), under `/api/actions/{action_id}/`:

| Path suffix | Supported for Excel |
| ----------- | ------------------- |
| `read` | Yes — `usedRange` or named A1 range |
| `update` | Yes — PATCH range with `values` (workbook session) |
| `append` | Yes — appends rows after used range |
| `clear` | Yes — clears listed ranges |
| `create` | Yes — creates new `.xlsx` under drive root via upload |
| `share` | Yes — link / user invite on the workbook item |
| DELETE (delete spreadsheet) | Yes — `spreadsheet_id` / `spreadsheet_url` |
| `worksheet/create` | Yes |
| `worksheet/update` | **Rename only** — body must include `new_title`; grid size / hidden / tab color not supported |
| `worksheet/delete` | Yes |
| `format` | **Not implemented** — calls raise `NotImplementedError` |
| `merge` / `unmerge` | **Not implemented** — same |

Use OpenAPI for full query/body fields (`spreadsheet_id`, `spreadsheet_url`, `range_name`, `worksheet_title`, etc.).

## Limitations vs Google Sheets

Graph Excel endpoints do not implement merge/unmerge or arbitrary cell formatting in this action; use values-only **update** / **clear** instead. **Worksheet update** is limited to renaming the tab (`new_title`).

## Workbook resolution

`resolve_workbook_item_id` accepts:

- A Graph URL containing `/items/{id}`
- A raw drive item id
- A Google Sheets URL is detected and treated as a Sheets id (for parity helpers)—for Excel you should pass a OneDrive-backed workbook
