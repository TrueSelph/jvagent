---
name: pdf_generation
description: >
  Renders a polished PDF from Markdown-like plain text. Prefers LaTeX
  (xelatex / pdflatex / lualatex, or Tectonic) for layout and typography; falls back
  to WeasyPrint (HTML-to-PDF) when no TeX engine is available. Optional
  Google Drive upload when a folder ID is supplied.
allowed-tools:
  - pdf_generation__latex_compile
  - pdf_generation__pandoc_fallback
  - pdf_generation__export_google_doc_pdf
version: 2
tags:
  - pdf
  - latex
  - document
  - publishing
---

## Workflow

1. Gather the final document: `title` and main `content` (Markdown-like headings, lists, paragraphs). All editorial decisions are upstream; this skill only renders.
2. If the approved source is a Google Doc, prefer `pdf_generation__export_google_doc_pdf`.
3. Otherwise prefer `pdf_generation__latex_compile` when a TeX engine is available (LaTeX or Tectonic).
4. If LaTeX is missing or the build fails with a fatal error, use `pdf_generation__pandoc_fallback` (requires WeasyPrint).
5. Optionally pass `drive_output_folder_id` to upload the PDF if `GoogleDriveAction` is enabled in the app.
6. Return the local `pdf_path` and, if applicable, the Drive `drive_upload` result.

### Inputs (generic)

- **title** (required): Document title.
- **content** (required): Main body. Legacy alias: `body`.
- **subtitle** (optional): e.g. audience, client, or project code for the cover. Legacy alias: `client_name`.
- **author** (optional): Organization or author line in header and cover. Legacy alias: `company_name`.
- **date**, **prepared_for_label**, **presented_by_label**: Optional copy and locale tweaks for the cover.
- **mark_confidential** (default: true): Include “CONFIDENTIAL” in LaTeX headers and on the cover where applicable.
- **output_basename** (optional): Filename stem for Google Drive upload (e.g. `Q1_Security_Review` → `Q1_Security_Review.pdf`).
- **brand_primary_color**, **brand_accent_color**, **brand_logo_path**, **company_letterhead**: Optional branding values for rendered PDF paths.

### Constraints

- Prefer LaTeX when possible for best typography. Use the fallback only for portability or if compilation fails and a fast HTML path is acceptable.
- Do not change or rewrite the source text during export; render the approved content.
- If LaTeX fails, include compile output from the tool response so the issue can be diagnosed.
- The PDF is a delivery artifact. Do not re-run generation unless the user or pipeline asks for a new build.

## Dependencies

- **LaTeX / Tectonic** (preferred): not installable via pip. Either install a full TeX distribution (TeX Live, MacTeX, MiKTeX) so `xelatex`, `pdflatex`, or `lualatex` is on `PATH`, or install the standalone [Tectonic](https://tectonic-typesetting.github.io/) CLI (`tectonic` on `PATH`) for a smaller footprint. See comments in `resources/requirements.txt` for typical install commands.
- **WeasyPrint** (fallback for `pdf_generation__pandoc_fallback`): add `weasyprint` to `package.dependencies.pip` on the app’s `ReasoningHelm` `info.yaml` (auto-install on action load), or `pip install weasyprint` manually. See `resources/requirements.txt` in this bundle. Some platforms need extra OS libraries (Cairo, Pango, etc.); follow [WeasyPrint install](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation) if import fails.

## Scope

HTML-to-PDF fallback is implemented with WeasyPrint, not Pandoc. The tool name is historical.

## Application examples

- **Proposals or client PDFs:** Set `subtitle` to the client name, `author` to your firm, and `output_basename` to your naming convention (e.g. `Proposal_Acme_20260426`). Same parameter names work for memos, reports, and SOPs.

## Grounding

- Treat tool parameters as authoritative; do not assume extra proposal-specific files or nodes exist unless the surrounding agent provides them.
- Drive upload only occurs when `drive_output_folder_id` is set and `GoogleDriveAction` resolves; otherwise the skill still returns a local `pdf_path`.
