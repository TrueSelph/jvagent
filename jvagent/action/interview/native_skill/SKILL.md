---
name: native-interview
description: Version one native interview domain procedure
requires-actions: [InterviewAction]
requires-tools: [interview__start]
allowed-tools:
  - interview__start
  - interview__set_fields
  - interview__skip_field
  - interview__field_unavailable
  - interview__next_field
  - interview__get_status
  - interview__review
  - interview__complete
  - interview__cancel
  - interview__reset
---
# Native interview procedure v1

Use the interview tools for the selected skill. Activation is an explicit call to
interview__start with this skill's name. Loading the skill alone does not start
or change an interview. No session means no field prompts: first activate and read
the returned field_reference and start_field.

Extract values only from the user's latest message. Use exact field_reference keys
in one interview__set_fields call for all supplied values. Activation stores no
values. Read ok and per-field validation outcomes before proceeding; correct errors
or ask the user rather than inventing values. Do not reuse earlier answers unless
the user supplies them again. Each following operation is a separate explicit tool
call. Call interview__next_field when more information is needed, and relay its
question. Use interview__get_status for the current catalog or progress.

Skip a declined optional field with interview__skip_field. For unavailable required
fields call interview__field_unavailable; follow its reported consequence. Handle
corrections through interview__set_fields, cancel through interview__cancel, and
starting over through interview__reset. Interpret the user's intent in this SOP;
there is no server intent classifier.

When collection is ready, call interview__review and show the actual review. For
manual confirmation, wait for the user's next message confirming that review before
calling interview__complete. Never confirm on the user's behalf or claim a task has
completed before the tool reports completion. After cancellation or completion,
use interview__start again for a new interview. Plain tool results and persisted
session/task state are authoritative; do not claim external side effects.
