---
name: handoff_staff
description: >-
  Save or correct a staff answer in the knowledge base. Use when the latest
  message answers a pending customer question, or corrects a saved answer
  whose chunk id is in an [EVENT] in history.
allowed-tools:
  - handoff__pending_questions
  - handoff__save_answer
  - handoff__update_chunk
requires-actions:
  - HandoffAction
extends: action:jvagent/handoff_action
access-action: HandoffAction
allowed-groups:
  - staff
version: 1
tags:
  - handoff
  - staff
  - knowledge
---

Call `use_skill` for this skill before any of its tools.

## Tools

- `handoff__pending_questions` — if the user message and the history look like an answer rather than a new question, call this first. Check if the user message is an answer to a pending question, then call `handoff__save_answer` to save the full answer only.
- `handoff__save_answer` — if one pending question matches, pass its `question_id` and the full answer. Relay the thank-you.
- `handoff__update_chunk` — correct a saved answer. Take `chunk_id` from the `[EVENT]` in history and the new answer from the user's message. Relay the confirmation.

## Saving an answer

If the latest message and the history look like an answer rather than a new
question, you do not know what is pending until you list it:

1. Call `handoff__pending_questions` first.
2. Check whether the user message answers one of the returned questions.
3. Call `handoff__save_answer` with that `question_id` and the full answer
   only. The tool stores the cleaned answer on the knowledge base.
4. Relay the tool's confirmation as-is. Do not repeat the answer, and do not
   try to deliver it to the customer from this thread.

Do not save an answer for a question that is not pending.

## Correcting a saved answer

When history has an `[EVENT]` with a handoff chunk id and the user is
correcting that answer, call `handoff__update_chunk` with that `chunk_id` and
the new answer. Relay the confirmation as-is.
