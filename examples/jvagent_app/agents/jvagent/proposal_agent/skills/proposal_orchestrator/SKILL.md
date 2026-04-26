---
name: proposal_orchestrator
description: >
  Orchestrates the full proposal pipeline in sequence: draft → price →
  author (Google Doc → Markdown fallback) → review → PDF (LaTeX →
  WeasyPrint fallback). Activates sub-skills in the correct order and
  manages state across stages. This is a meta-skill — it does not
  provide its own tools beyond the pipeline orchestration logic.
response-mode: respond
tags:
  - orchestration
  - proposal
  - pipeline
---

## Pipeline Stages

### Stage 1: Draft Generation

1. Activate the `proposal_draft` skill.
2. Call `proposal_draft__retrieve_specimens` with tags derived from the client context.
3. Review the returned template, guide, and available specimens.
4. Call `proposal_draft__generate_draft` with the transcript analysis and reference materials.
5. Present a summary of the draft to the user.

### Stage 2: Pricing

1. Activate the `pricing` skill.
2. Call `pricing__extract_parameters` with the transcript and draft analysis.
3. Review the extracted scope parameters.
4. Call `pricing__apply_pricing` with the rubric name (default: "standard") and extracted parameters.
5. Write the pricing narrative based on the assessment (explain the line items, total, and value).
6. Merge the pricing section into the draft, replacing the `[PRICING PLACEHOLDER]` marker.

### Stage 3: Authoring

1. Activate the `authoring` skill.
2. Determine the output format via fallback chain:
   - **Google Doc** (primary) — if `GoogleDocsAction` is configured and available.
   - **Local Markdown** (fallback) — if Google Docs is unavailable. The file is written to the `output_dir` configured on `proposal_skill_interact_action` (default: `{{ APP_DIR }}/agents/jvagent/proposal_agent/output`).
3. Call `authoring__google_docs_write` with the full proposal content (draft + pricing) and revision markers.
   - If Google Docs fails or is not configured, call `authoring__markdown_write` instead.
4. Use `authoring__track_revisions` to initialize the revision tracking list.
5. Present the document URL (or file path) to the user for review.

### Stage 4: Revision Loop

1. Enter a loop:
   a. Tell the user the document is ready for review.
   b. Call `authoring__handle_feedback` with `mode: "poll"` to check status.
   c. If the user has made changes or resolved comments, apply them:
      - Re-read the document content.
      - Update the DraftProposal data accordingly.
      - Call `authoring__handle_feedback` with `mode: "apply"`.
   d. If the user signals approval (via chat or `mode: "approve"`), proceed to Stage 5.
   e. If the user requests changes (e.g., adjust pricing, rewrite section):
      - Re-activate the relevant skill (pricing, proposal_draft).
      - Regenerate the affected content.
      - Update the document.
      - Re-enter the revision loop.
2. Max 5 revision cycles before escalating to the user.

### Stage 5: PDF Generation

1. Activate the `pdf_generation` skill.
2. Determine PDF engine via fallback chain:
   - **LaTeX or Tectonic** (primary) — call `pdf_generation__latex_compile` with `title`, `content`, `subtitle` (client), `author` (org), and `output_basename`. The tool uses `xelatex`/`pdflatex`/`lualatex` when present, otherwise `tectonic` if installed (e.g. `brew install tectonic`).
   - **WeasyPrint** (fallback) — if no TeX engine is available or compilation fails, call `pdf_generation__pandoc_fallback` with the same fields.
3. Both engines use a **sandbox-relative** `output_dir` (optional tool argument). If omitted, the pipeline uses `proposal_skill_interact_action.output_dir` (default `output`). **`pdf_path` in the tool result is always that sandbox path** (e.g. `output/Document_client_20260226.pdf`); temp build dirs are internal only.
4. If a `drive_output_folder_id` is configured, both engines upload the PDF to Google Drive after generation.
5. Deliver the **sandbox `pdf_path`** to the user (and Drive link if applicable)—do not cite host temp paths.

**Output delivery summary:**
- Final PDF lives in the user-scoped jvspatial workspace; `pdf_path` is sandbox-relative.
- If `drive_output_folder_id` is configured, Drive upload proceeds from the build artifact before the sandbox write.
- Present the sandbox path and any Drive URL; never present `/var/.../jvagent_pdf_*/document.pdf` as the canonical location.

## State Management

- Each stage produces a structured output that feeds into the next stage.
- The DraftProposal is the central state object that accumulates data across stages.
- Revision markers are tracked via `authoring__track_revisions`.
- The pipeline can pause after any stage (e.g., for user review of the draft before pricing).
- If the pipeline is interrupted (skill loop limits), the current stage should be checkpointed.

## Error Recovery

- If a tool call fails, retry once. If it fails again, report the error to the user and offer alternatives.
- If Google Docs authoring fails (action missing, auth error, API error), fall back to Markdown authoring.
- If TeX compilation fails or no engine is installed, fall back to WeasyPrint via `pdf_generation__pandoc_fallback`.
- If the specimen corpus is empty, generate the draft from built-in defaults.
- If pricing rubric is missing, create a basic rubric via PricingAction or use hardcoded defaults.

## Completion Criteria

The pipeline is complete when:
1. A PDF has been generated successfully, OR
2. The user has been given the document URL and explicitly approved, OR
3. An unrecoverable error has been reported to the user with next-step suggestions.
