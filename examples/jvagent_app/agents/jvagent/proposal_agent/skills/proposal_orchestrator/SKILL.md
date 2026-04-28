---
name: proposal_orchestrator
description: >
  Orchestrates the full proposal pipeline: draft → price → author →
  user review (hard stop) → PDF on explicit approval only. PDF generation
  must never begin automatically — it requires the user to send an
  explicit approval in their next message after reviewing the document.
response-mode: respond
tags:
  - orchestration
  - proposal
  - pipeline
---

## CRITICAL RULE — PDF Generation Gate

**PDF generation (Stage 5) must never start unless the user's most recent
message contains an explicit approval signal** such as "approve", "looks
good", "generate PDF", "go ahead", "finalize it", or equivalent clear
affirmative intent.

If no explicit approval is present in the user's current message:
- Do NOT activate `pdf_generation`.
- Do NOT call any pdf tool.
- Stop at the review stage and ask the user to review and confirm.

This gate is absolute. It overrides any pipeline flow logic below.

## Pipeline Stages

### Stage 0: Intake Completeness Gate

1. Confirm required intake fields: client name, project title, core needs, scope context, timeline signal, and commercial assumptions.
2. If any are missing, capture `[REVIEW: ...]` markers and request clarification before proceeding.

### Stage 1: Draft Generation

1. Activate the `proposal_draft` skill.
2. Call `proposal_draft__retrieve_specimens` with tags derived from the client context.
3. Review the returned template, guide, and selected specimen contents.
4. Call `proposal_draft__generate_draft` with the transcript analysis and reference materials.
5. Run `proposal_draft__quality_review` on the rendered markdown.
6. If quality score is below threshold or required sections are missing, regenerate before continuing.
7. Present a summary of the draft to the user.

### Stage 2: Pricing

1. Activate the `pricing` skill.
2. Call `pricing__extract_parameters` with the transcript and draft analysis.
3. Review the extracted scope parameters.
4. Call `pricing__apply_pricing` with the rubric name (default: "standard") and extracted parameters.
5. Call `pricing__build_investment_section` to deterministically replace `[PRICING PLACEHOLDER]`.
6. If pricing assumptions are unresolved, add review markers and request approval before final review.

### Stage 3: Authoring

1. Activate the `authoring` skill.
2. Determine the output format via fallback chain:
   - **Google Doc from template** (primary) — if `GoogleDocsAction` and template config are available.
   - **Google Doc (blank)** — if Google Docs is available but no template is configured.
   - **Local Markdown** (fallback) — if Google Docs is unavailable. The file is written to the `output_dir` configured on `proposal_skill_interact_action` (default: `{{ APP_DIR }}/agents/jvagent/proposal_agent/output`).
3. Call `authoring__google_docs_write` with the full proposal content, placeholders, and revision markers.
   - If Google Docs fails or is not configured, call `authoring__markdown_write` instead.
4. Use `authoring__track_revisions` to initialize the revision tracking list.
5. Capture a baseline snapshot via `authoring__snapshot_document`.
6. **HARD STOP — Respond to the user now.** Present the document URL or file path with a message similar to:
   > "Your proposal draft is ready for review. You can view it at [URL/path]. Please review the document, then reply with **'approve'** when you're satisfied (or request changes you'd like made)."
7. **Do not proceed to Stage 4 or Stage 5 within this turn.** The pipeline pauses here and waits for the user's next message.

### Stage 4: Revision Loop

This stage begins only when the user replies after Stage 3 (or a previous revision cycle).

**At the start of each Stage 4 turn:**

1. Call `authoring__check_approval_signal` with the user's current message.
   - If `approved=true`, skip Stage 4 entirely and go directly to Stage 5.
   - If `approved=false`, treat the message as a revision request or question and proceed below.

**Handling revision requests:**

1. Call `authoring__handle_feedback` with `mode: "poll"` to read the current document content and detect changes.
2. If the user made direct edits to the document (detected via hash change):
   - Call `authoring__diff_against_snapshot` to surface what changed.
   - Update the proposal state accordingly.
3. If the user sent a chat revision request (e.g. "make the summary more concise", "adjust the timeline"):
   - Call `authoring__apply_revision_request` with the request and current content.
   - Regenerate the affected content and rewrite the review artifact.
   - For pricing changes, re-activate the `pricing` skill and rebuild the Investment section.
4. Refresh the baseline snapshot via `authoring__snapshot_document`.
5. **HARD STOP — Respond to the user again.** Summarize what was changed and re-present the document URL or updated content. Ask again for approval:
   > "I've updated the proposal. Here's the revised document: [URL/path]. Reply **'approve'** to generate the PDF, or let me know if you'd like further changes."
6. **Do not proceed to Stage 5 within this turn.** Wait for the next user message.

Max 5 revision cycles. After 5 unapproved cycles, respond and ask the user how they would like to proceed.

### Stage 5: PDF Generation

**Entry guard (mandatory before any pdf tool):**

1. Call `authoring__check_approval_signal` with the user's current message.
2. If `approved=false`, do NOT proceed. Respond to the user with the document URL/path and ask them to confirm: "Reply 'approve' or 'generate PDF' when you're ready." Then stop.
3. If `approved=true`, proceed.

Once the entry guard passes:

1. Call `authoring__handle_feedback` with `mode: "approve"` to record the approval in state.
2. Activate the `pdf_generation` skill.
3. Determine source-aware PDF engine via fallback chain:
   - **Google Docs export** (primary when source is Google Doc) — call `pdf_generation__export_google_doc_pdf`.
   - **LaTeX or Tectonic** (primary for markdown source) — call `pdf_generation__latex_compile`.
   - **WeasyPrint** (fallback) — call `pdf_generation__pandoc_fallback` if TeX is unavailable.
4. Both engines use a **sandbox-relative** `output_dir`. **`pdf_path` in the tool result is always that sandbox path** (e.g. `output/Document_client_20260226.pdf`).
5. If a `drive_output_folder_id` is configured, upload the PDF to Google Drive after generation.
6. Deliver the **sandbox `pdf_path`** to the user (and Drive link if applicable).

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
1. A PDF has been generated successfully after explicit user approval, OR
2. The user declines PDF generation and has been given the final document URL, OR
3. An unrecoverable error has been reported to the user with next-step suggestions.

## Turn Boundaries

Each stage boundary is a turn boundary. The pipeline must not span Stage 3 → Stage 4, or Stage 4 → Stage 5, in a single model turn. Every stage exit requires a response to the user and a hard stop. The next stage begins only in response to the user's next message.
